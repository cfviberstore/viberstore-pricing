"""
Pricing engine - pure functions, no network, fully unit-tested.

Input per model:  vs[(cond, storage)]  -> {price, in_stock, price_spread}
                  ref[(cond, storage)] -> {price, in_stock, url} | None | "error"
Output per variant: comparison, status, flags, and a recommendation.

Rules (all thresholds live in config.json -> policy):
  * Every recommendation carries a kind (market / capped / ladder / manual_review), a plain-English
    reason and a confidence level (see confidence()). Ladder fixes obey the raise limits.
  * Within +-tolerance of Refurbed            -> competitive, hold.
  * ViberStore dearer by more than tolerance  -> lower to the highest X9.99 at or below (Refurbed - undercut).
  * ViberStore cheaper by more than tolerance -> raise to the highest X9.99 at or below (Refurbed - undercut),
    but never by more than max_raise_pct in one step.
  * Prices land on the X9.99 ladder you already use.
  * Ladder: Good < V.Good < Excellent (>= min_condition_gap) and smaller storage <
    larger storage (>= min_storage_gap).  Violations - including ones already live on
    the shop - are corrected by raising the offending price.
  * Refurbed out of stock, or no Refurbed data -> no market recommendation.
  * Gaps over review_gap_pct are flagged 'review' (usually a config mismatch, not a
    real opportunity).
"""
from dataclasses import dataclass

CONDITIONS = ["Good", "V. Good", "Excellent"]


@dataclass
class Policy:
    tolerance_eur: float = 10
    undercut_eur: float = 0
    max_raise_pct: float = 10
    max_lower_pct: float = 30
    min_condition_gap_eur: float = 10
    min_storage_gap_eur: float = 20
    review_gap_pct: float = 35

    @classmethod
    def from_config(cls, d):
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


def storage_gb(s):
    n = int("".join(c for c in s if c.isdigit()))
    return n * 1000 if s.upper().endswith("TB") else n


def snap_x999(price):
    """Nearest X9.99 (667.50 -> 669.99, 450.99 -> 449.99)."""
    return round(round((price + 0.01) / 10) * 10 - 0.01, 2)


def snap_x999_up(price):
    """Smallest X9.99 that is >= price."""
    import math
    return round(math.ceil((price + 0.01) / 10 - 1e-9) * 10 - 0.01, 2)


def snap_x999_down(price):
    """Largest X9.99 that is <= price (used for hard limits, which must never be exceeded)."""
    import math
    return round(math.floor((price + 0.01) / 10 + 1e-9) * 10 - 0.01, 2)


def market_target(ref_price, p):
    """The highest X9.99 price that is at or below (reference - undercut).

    undercut_eur = 0 (default) matches Refurbed: 499.99 -> 499.99, 505 -> 499.99, 512 -> 509.99, 498 -> 489.99.
    undercut_eur = 5 or 10 sits that far below before rounding down to X9.99.
    """
    return snap_x999_down(ref_price - p.undercut_eur)


def _direction(current, new):
    diff = new - current
    return "up" if diff > 5 else "down" if diff < -5 else "same"


CONF_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}
# Evidence gaps that make a comparison unreliable on their own (-> low confidence).
SEVERE = {"stale_data": "Refurbed price is from an earlier run",
          "single_offer": "Only one comparable offer found",
          "ref_anomaly": "Refurbed's own grades or storages are out of order",
          "review_large_gap": "Gap is unusually large"}
# Gaps that cap confidence at medium.
MINOR = {"lone_cheap_offer": "Cheapest offer is well below the next one",
         "same_seller_only": "All offers seen are from one seller",
         "spec_unverified": "Battery or SIM details missing on the cheapest offer",
         "no_supported_price": "No price backed by two independent sellers"}


def confidence(row):
    """High / medium / low plus the evidence that is missing. Pure function of the row."""
    if row["ref_price"] is None:
        return {"level": "none", "missing": ["No comparable Refurbed offer"]}
    f = set(row["flags"])
    if row.get("ref_supported") is None and not ({"single_offer", "same_seller_only"} & f):
        f.add("no_supported_price")
    severe = [SEVERE[k] for k in SEVERE if k in f]
    minor = [MINOR[k] for k in MINOR if k in f]
    level = "low" if severe else "medium" if minor else "high"
    return {"level": level, "missing": severe + minor}


def _eur(x):
    return f"\u20ac{abs(x):,.0f}"


def _reason(row, rec, policy):
    r, v = row["ref_price"], row["vs_price"]
    kind = rec["kind"]
    if kind == "manual_review":
        return rec["why_blocked"]
    if kind == "ladder":
        return rec["why_ladder"]
    base = (f"Refurbed's cheapest comparable offer is {_eur(row['delta'])} "
            f"{'above' if row['delta'] < 0 else 'below'} you ({abs(row['delta_pct']):.1f}%)")
    if kind == "capped":
        return (f"{base}. The market target is {_eur(rec['ideal_price'])}, but one step is limited to "
                f"{policy.max_lower_pct:g}% down." if rec["direction"] == "down" else
                f"{base}. The market target is {_eur(rec['ideal_price'])}, but one step is limited to "
                f"{policy.max_raise_pct:g}% up.")
    return base + (". Moving to just under it." if rec["direction"] == "up" else ". Moving to just under it.")


def build_model(vs, ref, policy, guards=()):
    """Returns a list of variant dicts (sorted by storage, condition).

    guards: optional callables f(row, policy) run last, e.g. a cost-based margin guard.
    They may annotate row['flags'] or adjust row['recommended'] (kind/reason included).
    """
    keys = sorted(vs.keys() | {k for k, v in ref.items() if isinstance(v, dict) and v.get("price") is not None},
                  key=lambda k: (storage_gb(k[1]), CONDITIONS.index(k[0])))
    rows = {}
    for key in keys:
        cond, storage = key
        v, r = vs.get(key), ref.get(key)
        row = {
            "condition": cond, "storage": storage,
            "vs_price": v["price"] if v else None,
            "vs_in_stock": v["in_stock"] if v else None,
            "ref_price": None, "ref_in_stock": None, "ref_url": None,
            "ref_floor": None, "ref_typical": None, "ref_supported": None, "ref_sellers": None,
            "ref_offers": None, "ref_excluded": None, "ref_excluded_from": None, "ref_checked": None,
            "delta": None, "delta_pct": None,
            "status": "no_ref_data", "flags": [], "recommended": None, "confidence": None,
        }
        if v and v.get("price_spread", 0) >= 5:
            row["flags"].append("mixed_colour_prices")
        if r == "error":
            row["flags"].append("ref_fetch_failed")
        elif isinstance(r, dict):
            row["flags"] += r.get("flags", [])
            row["ref_floor"], row["ref_typical"] = r.get("floor"), r.get("typical")
            row["ref_supported"], row["ref_sellers"] = r.get("supported"), r.get("sellers")
            row["ref_offers"], row["ref_excluded"] = r.get("offers"), r.get("excluded")
            row["ref_excluded_from"] = r.get("excluded_from") or None
            row["ref_checked"] = r.get("checked")
            if r.get("price") is not None:
                row["ref_price"], row["ref_in_stock"], row["ref_url"] = r["price"], r.get("in_stock", True), r["url"]
        rows[key] = row

    _flag_ref_anomalies(rows)

    # ---- market-based targets ------------------------------------------------------
    for row in rows.values():
        v, r = row["vs_price"], row["ref_price"]
        if v is None or r is None:
            continue
        row["delta"] = round(v - r, 2)
        row["delta_pct"] = round((v - r) / r * 100, 1)
        if row["ref_in_stock"] is False:
            row["status"] = "ref_out_of_stock"
            row["flags"].append("ref_out_of_stock")
            continue
        if abs(row["delta"]) > policy.review_gap_pct / 100 * r:
            row["flags"].append("review_large_gap")
        if row["delta"] > policy.tolerance_eur:
            row["status"] = "refurbed_cheaper"
            # Lowering: don't chase a lone cheap offer - aim at the price a second seller backs.
            basis = next((x for x in (row["ref_supported"], row["ref_typical"]) if x is not None), r)
            ideal = market_target(basis, policy)
            target = min(max(ideal, snap_x999_up(v * (1 - policy.max_lower_pct / 100))), v)
            was_capped = target > ideal
        elif row["delta"] < -policy.tolerance_eur:
            row["status"] = "vs_cheaper"
            # Raising: the reference is the cheapest comparable offer, so never price above it.
            ideal = market_target(r, policy)
            capped = snap_x999_down(v * (1 + policy.max_raise_pct / 100))
            target = max(min(ideal, max(capped, v)), v)
            was_capped = target < ideal
        else:
            row["status"] = "competitive"
            continue
        if abs(target - v) >= 0.5:
            rec = {"price": target, "reasons": ["market"], "kind": "capped" if was_capped else "market"}
            if was_capped:
                rec["ideal_price"] = ideal
            row["recommended"] = rec

    # ---- price ladder (applies to the price we'd have AFTER the recommendation) ------
    # A ladder fix is a raise, so it obeys the same limits as any other raise: never more than
    # max_raise_pct above today's price and never above what Refurbed's cheapest comparable offer
    # (plus tolerance) lets us charge. When the ladder needs more than that, nothing is applied
    # silently: the row becomes a manual_review item carrying the price the ladder wants.
    def eff(row):
        rec = row["recommended"]
        return rec["price"] if rec and rec["kind"] != "manual_review" else row["vs_price"]

    storages = sorted({k[1] for k in rows}, key=storage_gb)
    for si, st in enumerate(storages):
        for ci, cond in enumerate(CONDITIONS):
            row = rows.get((cond, st))
            if not row or row["vs_price"] is None:
                continue
            final = eff(row)
            floor, why, because = final, None, None
            if ci > 0:
                prev = rows.get((CONDITIONS[ci - 1], st))
                if prev and prev["vs_price"] is not None and eff(prev) + policy.min_condition_gap_eur > floor:
                    floor, why = eff(prev) + policy.min_condition_gap_eur, "ladder_condition"
                    because = f"{CONDITIONS[ci - 1]} is {_eur(eff(prev))}, so {cond} needs at least {_eur(floor)}"
            if si > 0:
                prev_st = next((s for s in reversed(storages[:si]) if (cond, s) in rows
                                and rows[(cond, s)]["vs_price"] is not None), None)
                if prev_st and eff(rows[(cond, prev_st)]) + policy.min_storage_gap_eur > floor:
                    floor, why = eff(rows[(cond, prev_st)]) + policy.min_storage_gap_eur, "ladder_storage"
                    because = f"{prev_st} is {_eur(eff(rows[(cond, prev_st)]))}, so {st} needs at least {_eur(floor)}"
            if not why:
                continue
            want = snap_x999_up(floor)
            if want <= final + 0.005:
                continue
            row["flags"].append("ladder_violation")
            limit = max(snap_x999_down(row["vs_price"] * (1 + policy.max_raise_pct / 100)), final)
            limit_why = f"{policy.max_raise_pct:g}% raise limit"
            if row["ref_price"] is not None and row["ref_in_stock"] is not False:
                market_cap = max(snap_x999_down(row["ref_price"] + policy.tolerance_eur), final)
                if market_cap < limit:
                    limit, limit_why = market_cap, "Refurbed price (plus tolerance)"
            if want <= limit + 0.005:
                rec = row["recommended"]
                if rec and rec["kind"] != "manual_review":
                    rec["price"] = want
                    rec["reasons"].append(why)
                    rec["kind"] = "ladder"
                    rec.pop("ideal_price", None)
                else:
                    rec = {"price": want, "reasons": [why], "kind": "ladder"}
                rec["why_ladder"] = f"Price ladder: {because}."
                row["recommended"] = rec
            else:
                row["flags"].append("ladder_unresolved")
                msg = (f"Price ladder: {because}, but that is more than the {limit_why} allows "
                       f"(up to {_eur(limit)}). Decide by hand which price to move.")
                rec = row["recommended"]
                if rec and rec["kind"] != "manual_review":
                    # keep the market suggestion as the price shown; the ladder conflict is the reason it needs a person
                    rec.update(kind="manual_review", why_blocked=msg, ladder_wants=want, limit_price=limit)
                    rec["reasons"].append(why)
                else:
                    row["recommended"] = {"price": want, "reasons": [why], "kind": "manual_review",
                                          "why_blocked": msg, "ladder_wants": want, "limit_price": limit}

    for row in rows.values():
        rec = row["recommended"]
        if rec:
            rec["change"] = round(rec["price"] - row["vs_price"], 2)
            rec["direction"] = _direction(row["vs_price"], rec["price"])
            if rec["direction"] == "same":
                row["recommended"] = None
        row["flags"] = sorted(set(row["flags"]))
        row["confidence"] = confidence(row)
        rec = row["recommended"]
        if rec:
            # Suspicious market data turns a plain market move into a review item (price kept for context).
            if rec["kind"] in ("market", "capped") and {"ref_anomaly", "review_large_gap"} & set(row["flags"]):
                rec["why_blocked"] = ("The gap to Refurbed is unusually large or Refurbed's own prices look inconsistent; "
                                      "check the offers before moving this price.")
                rec["kind"] = "manual_review"
            rec["reason"] = _reason(row, rec, policy)
            rec["confidence"] = row["confidence"]["level"]
        row["queue"] = ("none" if not rec else "action" if row["vs_in_stock"] else "maintenance")
        for g in guards:
            g(row, policy)
        row["priority"] = _priority(row)
    return list(rows.values())


def _flag_ref_anomalies(rows):
    """Refurbed prices that break the natural ladder are suspicious (stale offer,
    odd configuration). Flag both ends so the UI can show 'verify'."""
    by_storage = {}
    for (cond, st), r in rows.items():
        if r["ref_price"] is not None:
            by_storage.setdefault(st, {})[cond] = r
    for st, conds in by_storage.items():
        seq = [(c, conds[c]) for c in CONDITIONS if c in conds]
        for (c1, r1), (c2, r2) in zip(seq, seq[1:]):
            if r2["ref_price"] < r1["ref_price"] - 0.5:
                r1["flags"].append("ref_anomaly"); r2["flags"].append("ref_anomaly")
    by_cond = {}
    for (cond, st), r in rows.items():
        if r["ref_price"] is not None:
            by_cond.setdefault(cond, []).append((storage_gb(st), r))
    for cond, lst in by_cond.items():
        lst.sort(key=lambda x: x[0])
        for (_, r1), (_, r2) in zip(lst, lst[1:]):
            if r2["ref_price"] < r1["ref_price"] - 0.5:
                r1["flags"].append("ref_anomaly"); r2["flags"].append("ref_anomaly")


def _priority(row):
    """Sort key for the daily queue: bigger moves with better evidence first."""
    rec = row["recommended"]
    if not rec:
        return 0
    score = abs(rec["change"])
    score *= {"high": 1.0, "medium": 0.7, "low": 0.4, "none": 0.3}[(row["confidence"] or {}).get("level", "none")]
    if rec["kind"] == "manual_review":
        score *= 0.6
    if row["vs_in_stock"] is False:
        score *= 0.2
    return round(score, 1)


def summarise(models):
    rows = [dict(r, brand=m["brand"], model=m["model"]) for m in models for r in m["variants"]]
    comparable = [r for r in rows if r["delta"] is not None and r["status"] != "ref_out_of_stock"]
    wins = [r for r in comparable if r["delta"] <= 0]
    actions = [r for r in rows if r["recommended"]]
    in_stock_actions = [r for r in actions if r["vs_in_stock"]]
    return {
        "models": len(models),
        "variants": len(rows),
        "comparable": len(comparable),
        "vs_cheaper": sum(r["status"] == "vs_cheaper" for r in rows),
        "refurbed_cheaper": sum(r["status"] == "refurbed_cheaper" for r in rows),
        "competitive": sum(r["status"] == "competitive" for r in rows),
        "no_ref_data": sum(r["status"] == "no_ref_data" for r in rows),
        "win_rate": round(100 * len(wins) / len(comparable), 1) if comparable else None,
        "avg_delta": round(sum(r["delta"] for r in comparable) / len(comparable), 2) if comparable else None,
        "avg_delta_pct": round(sum(r["delta_pct"] for r in comparable) / len(comparable), 1) if comparable else None,
        "actions": len(actions),
        "actions_in_stock": len(in_stock_actions),
        "ladder_violations": sum("ladder_violation" in r["flags"] for r in rows),
        "review_flags": sum(bool({"review_large_gap", "ref_anomaly"} & set(r["flags"])) for r in rows),
        "queue": sum(r["queue"] == "action" for r in rows),
        "maintenance": sum(r["queue"] == "maintenance" for r in rows),
        "by_kind": {k: sum(bool(r["recommended"]) and r["recommended"]["kind"] == k and r["queue"] == "action" for r in rows)
                    for k in ("market", "capped", "ladder", "manual_review")},
        "by_confidence": {k: sum(bool(r["recommended"]) and r["queue"] == "action" and r["confidence"]["level"] == k
                                 for r in rows) for k in ("high", "medium", "low", "none")},
        "lone_offers_ignored": sum("lone_cheap_offer" in r["flags"] for r in rows),
        "no_comparable": sum("no_comparable_offer" in r["flags"] for r in rows),
    }
