"""
Refurbed.ie scraper (v2 - exact variant prices).

Why this exists in its current form
-----------------------------------
v1 read ONE page per model and rebuilt every storage/condition price by adding the
"+EUR x" deltas shown in the dropdowns.  Those deltas are relative to whatever
combination the page happens to be showing and are NOT additive across storage and
condition, so large storages were wildly wrong (e.g. iPhone 13 Pro 1TB Good: EUR1,024
stored vs EUR485 real).

v2 fetches the real page for every storage x condition ViberStore actually sells and
reads the price from that page's schema.org BuyAction JSON-LD.  Every variant has its
own URL:   /p/<slug>/<storage-id><grade-suffix>/     (suffix: c=Good, b=Very good,
'' = Excellent, aa = Premium).  The ids and suffixes are read from the page's own
dropdowns, never hard-coded.

v3 - comparable offers only
---------------------------
Refurbed is a marketplace: each page shows the "buy box" winner among many sellers, and
that offer can differ in ways that don't match what ViberStore sells (new-battery
premium offers, eSIM-only phones, rare colours).  For every storage x condition we now:
  1. read the buy-box offer's own metadata (SIM type, battery condition, seller, colour)
     from the page's embedded `gtmData`,
  2. discard offers that are not comparable (configurable: new battery, eSIM-only,
     out of stock) - for a new-battery buy box we follow the page's own "Optimal" link,
  3. verify the cheapest few colours (colours are not priced separately by ViberStore, so
     any colour counts) and take the cheapest comparable offer as the market floor,
  4. use the cheapest comparable offer as the market price, and flag it ("lone_cheap_offer")
     when no other offer is near it, so one odd listing can't trigger a price cut.
Pages under /o/ (the "all offers" list) are disallowed by Refurbed's robots.txt and are
never requested.

Safety checks: the page's JSON-LD URL must match the URL we asked for (otherwise
Refurbed redirected us to a different configuration and we discard it), and a model
slug is only accepted when Refurbed's product name matches ours.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from http_client import Client

BASE_URL = "https://www.refurbed.ie"

GRADE_NORMALISE = {"good": "Good", "very good": "V. Good", "excellent": "Excellent"}
# "premium" is intentionally ignored - ViberStore has no equivalent grade.
# Only used when a grade is missing from the dropdown for the currently selected storage;
# the redirect check in _combo_from_html rejects the result if the guess was wrong.
DEFAULT_SUFFIX = {"Good": "c", "V. Good": "b", "Excellent": ""}

_SELECT_RE = re.compile(
    r'<select\s+id="(product-grade|product-storage|product-color|product-battery)"[^>]*>(.*?)</select>', re.S
)
DEFAULT_RC = {
    "verify_colours": 3,            # comparable offers to confirm per storage x grade
    "max_page_fetches": 6,          # hard cap on extra pages per storage x grade
    "exclude_battery": ["new"],     # ViberStore's standard grades are standard-battery phones
    "exclude_sim_pattern": r"(2\s*e-?sims?|e-?sim\s*only|only\s*e-?sim|dual[- ]e-?sim)",
    "lone_gap_eur": 15,             # cheapest offer this far below the next = "lone"
    "lone_gap_pct": 6,
}
_OPTION_RE = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.S)
_VARIANT_URL_RE = re.compile(r"/p/([^/]+)/(\d+)([a-z]*)/")
_BUY_ACTION_RE = re.compile(
    r'data-test="product-buy-action-schema">(.*?)</script>', re.S
)


# --------------------------------------------------------------------------- parsing
def normalise_storage(label):
    s = re.sub(r"\s+", "", label or "").upper()
    m = re.fullmatch(r"(\d+)(GB|TB)", s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "GB" and n >= 1000 and n % 1000 == 0:
        n, unit = n // 1000, "TB"
    return f"{n}{unit}"


def parse_delta(attr):
    """'more,+EUR55.00' -> 55.0, 'less,-EUR3.00' -> -3.0, '' -> 0.0"""
    if not attr:
        return 0.0
    m = re.search(r"([+-])\D*([\d.,]+)", attr)
    if not m:
        return 0.0
    num = m.group(2).replace(",", "")
    try:
        return (1 if m.group(1) == "+" else -1) * float(num)
    except ValueError:
        return 0.0


def _attr(attrs, name):
    m = re.search(rf'{name}="([^"]*)"', attrs)
    return m.group(1) if m else None


def parse_selects(html):
    """Returns {'grades': {cond: suffix}, 'storages': {storage: stem},
                'selected': (cond, storage) | None, 'colour_min_delta': float,
                'colours':   [{name, delta, url, selected}]   (main group only),
                'batteries': [{label, delta, url, selected, other}] }"""
    out = {"grades": {}, "storages": {}, "selected": None, "colour_min_delta": 0.0,
           "colours": [], "batteries": []}
    sel_grade = sel_storage = None
    for m in _SELECT_RE.finditer(html):
        kind, whole = m.group(1), m.group(2)
        body = whole.split("<optgroup")[0]  # main group; the rest = "other configurations"
        rest = whole[len(body):]
        deltas = []
        for om in _OPTION_RE.finditer(body):
            attrs, text = om.group(1), re.sub(r"\s+", " ", om.group(2)).strip()
            val = _attr(attrs, "value") or ""
            is_sel = "selected" in attrs
            delta = parse_delta(_attr(attrs, "data-price"))
            deltas.append(delta)
            um = _VARIANT_URL_RE.search(val)
            if kind == "product-grade":
                cond = GRADE_NORMALISE.get(text.lower())
                if cond and um:
                    out["grades"][cond] = um.group(3)
                    if is_sel:
                        sel_grade = cond
            elif kind == "product-storage":
                st = normalise_storage(text)
                if st and um:
                    out["storages"][st] = um.group(2)
                    if is_sel:
                        sel_storage = st
            elif kind == "product-color" and val:
                out["colours"].append({"name": text, "delta": delta, "url": val, "selected": is_sel})
            elif kind == "product-battery" and val:
                out["batteries"].append({"label": text, "delta": delta, "url": val, "selected": is_sel, "other": False})
        if kind == "product-battery":  # the other battery option may sit in "other configurations"
            for om in _OPTION_RE.finditer(rest):
                attrs, text = om.group(1), re.sub(r"\s+", " ", om.group(2)).strip()
                val = _attr(attrs, "value") or ""
                if val:
                    out["batteries"].append({"label": text, "delta": parse_delta(_attr(attrs, "data-price")),
                                             "url": val, "selected": False, "other": True})
        if kind == "product-color" and deltas:
            out["colour_min_delta"] = min(0.0, min(deltas))
    if sel_grade and sel_storage:
        out["selected"] = (sel_grade, sel_storage)
    return out


def parse_gtm(html):
    """The page embeds `var gtmData = {...item: {...}}` describing the displayed offer."""
    i = html.find("var gtmData = ")
    if i < 0:
        return {}
    try:
        return json.JSONDecoder().raw_decode(html[i + len("var gtmData = "):])[0].get("item", {})
    except (ValueError, AttributeError):
        return {}


def parse_variant_page(html):
    """Price + what kind of offer it is. None if the page has no usable offer."""
    offer = parse_offer(html)
    if not offer:
        return None
    g = parse_gtm(html)
    parts = [p.strip() for p in str(g.get("item_variant", "")).split("|")]
    sim = next((p for p in parts if re.search(r"sim", p, re.I)), None)
    seller = re.search(r'data-test="merchant-name"[^>]*>\s*([^<]+?)\s*<', html)
    ships = re.search(r'data-test="ships-from"[^>]*>\s*([^<]+?)\s*<', html)
    return {**offer, "sim": sim, "battery": g.get("battery_condition"), "colour": g.get("color"),
            "seller": seller.group(1) if seller else None,
            "ships_from": ships.group(1) if ships else (g.get("merchant_location") or None)}


def usable_offer(pg, rc):
    """(ok, reason) - is this buy-box offer comparable with what ViberStore sells?"""
    if not pg["in_stock"]:
        return False, "out_of_stock"
    if (pg.get("battery") or "").lower() in [b.lower() for b in rc["exclude_battery"]]:
        return False, "new_battery"
    if pg.get("sim") and re.search(rc["exclude_sim_pattern"], pg["sim"], re.I):
        return False, "esim_only"
    return True, None


def choose_reference(prices, rc):
    """prices: comparable offer prices. Returns (floor, typical, flags).

    floor   = cheapest comparable offer in any colour. ViberStore doesn't price by colour, so
              this is what a shopper can really get; it is the market price we compare with.
    typical = the cheapest price corroborated by a second offer. It differs from the floor
              only when the cheapest offer is a lone one (flag 'lone_cheap_offer'), which may
              be a single unit about to sell out. Recommendations never CUT a price to chase
              a lone offer, but the gap to it is still shown.
    """
    ps = sorted(prices)
    if not ps:
        return None, None, ["no_comparable_offer"]
    if len(ps) == 1:
        return ps[0], ps[0], ["single_offer"]
    gap = ps[1] - ps[0]
    if gap > max(rc["lone_gap_eur"], rc["lone_gap_pct"] / 100 * ps[0]):
        return ps[0], ps[1], ["lone_cheap_offer"]
    return ps[0], ps[0], []


def supported_price(offers, rc):
    """Cheapest price backed by at least two *different* sellers within the lone-offer gap.

    Several colours from one merchant are one opinion, not three, so seller identity matters.
    Offers without a seller name can't count as independent. Returns None when no price has
    independent support (we only ever see each colour's buy-box seller, so this is a floor on
    how well-supported a price is, not a full seller census).
    """
    ps = sorted((o["price"], o.get("seller")) for o in offers)
    for i, (p, s) in enumerate(ps):
        band = max(rc["lone_gap_eur"], rc["lone_gap_pct"] / 100 * p)
        sellers = {sel for pr, sel in ps[i:] if pr <= p + band and sel}
        if len(sellers) >= 2:
            return p
    return None


def parse_offer(html):
    """Reads the BuyAction JSON-LD. Returns dict(price, in_stock, url, name) or None."""
    m = _BUY_ACTION_RE.search(html)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))["object"]
        offer = obj["offers"]
        price = float(offer["price"])
    except (ValueError, KeyError, TypeError):
        return None
    if price <= 0:
        return None
    return {
        "price": round(price, 2),
        "in_stock": str(offer.get("availability", "")).endswith("InStock"),
        "url": offer.get("url", ""),
        "name": obj.get("name", ""),
    }


def canonical_name(s):
    """'Samsung Galaxy S25+' / 'galaxy s25 plus 5G' -> 's25plus' - for match-checking."""
    s = (s or "").lower().replace("+", " plus ")
    s = re.sub(r"\(.*?\)", lambda m: m.group(0).strip("()"), s)
    s = re.sub(r"\b(samsung|apple|galaxy|5g|4g)\b", " ", s)
    s = re.sub(r"\bz\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


# --------------------------------------------------------------------------- slugs
def slug_candidates(title):
    t = title.lower().replace("+", " plus ")
    t = re.sub(r"\((\d{4})\)", r"\1", t)
    t = re.sub(r"^apple\s+", "", t)
    words = re.sub(r"[^a-z0-9 ]", " ", t).split()
    variants = {tuple(words)}

    def add(ws):
        variants.add(tuple(ws))

    for ws in list(variants):  # with / without 5G (Refurbed often adds it)
        if "5g" in ws:
            add([w for w in ws if w != "5g"])
        elif ws and ws[0] in ("samsung", "galaxy") and "fold" not in " ".join(ws) and "flip" not in " ".join(ws):
            add(list(ws) + ["5g"])
    for ws in list(variants):  # Fold 7 <-> Fold7, add the 'z' Samsung uses
        out = []
        for i, w in enumerate(ws):
            nxt = ws[i + 1] if i + 1 < len(ws) else ""
            if w in ("fold", "flip") and nxt.isdigit():
                out.append(w + nxt)
            elif i > 0 and ws[i - 1] in ("fold", "flip") and w.isdigit():
                continue
            else:
                out.append(w)
        add(out)
    for ws in list(variants):
        for i, w in enumerate(ws):
            if w.startswith(("fold", "flip")) and (i == 0 or ws[i - 1] != "z"):
                add(list(ws[:i]) + ["z"] + list(ws[i:]))
    slugs = []
    for ws in variants:
        sl = "-".join(ws)
        if sl.startswith("galaxy-"):  # Refurbed prefixes Samsung slugs with 'samsung-'
            sl = "samsung-" + sl
        slugs.append(sl)
    return sorted(set(slugs), key=lambda s: (len(s), s))


class Refurbed:
    def __init__(self, interval=0.25, workers=4, rc=None):
        self.client = Client(BASE_URL, interval=interval)
        self.workers = workers
        self.rc = {**DEFAULT_RC, **(rc or {})}

    # ---- slug resolution (cached in data/slugs.json) -------------------------------
    def resolve_slug(self, title, cache, overrides=None, recheck_days=14):
        overrides = overrides or {}
        if title in overrides:
            return overrides[title]
        hit = cache.get(title)
        if hit:
            age = (date.today() - date.fromisoformat(hit["checked"])).days
            if hit["slug"] or age < recheck_days:
                return hit["slug"]
        want = canonical_name(title)
        found = None
        for slug in slug_candidates(title):
            status, html, final = self.client.get(f"/p/{slug}/")
            if status != 200 or f"/p/{slug}/" not in final:
                continue
            offer = parse_offer(html)
            if offer and canonical_name(offer["name"]) == want:
                found = slug
                break
        cache[title] = {"slug": found, "checked": date.today().isoformat()}
        return found

    # ---- price scraping ---------------------------------------------------------------
    def _fetch_page(self, path, expect):
        """Fetch one variant page and parse it. Returns (page, html) | 'error' | None.
        Pages whose own selected grade/storage differ from `expect` are rejected."""
        status, html, _ = self.client.get(path)
        if status == 0:
            return "error"
        if status != 200:
            return None
        pg = parse_variant_page(html)
        if not pg:
            return None
        sel = parse_selects(html)
        if expect and sel["selected"] and tuple(sel["selected"]) != tuple(expect):
            return None
        return pg, html

    def _combo(self, slug, stem, suffix, expect):
        path = f"/p/{slug}/{stem}{suffix}/"
        got = self._fetch_page(path, expect)
        if got in (None, "error"):
            return got
        pg, html = got
        if not pg["url"].split("#")[0].rstrip("/").endswith(path.rstrip("/")):
            return None  # redirected to another configuration - don't trust it
        return self.evaluate(path, pg, html, expect)

    def evaluate(self, path, pg, html, expect):
        """Given the landing page of a storage x grade, find the comparable market price."""
        rc = self.rc
        sel = parse_selects(html)
        items = [{"delta": 0.0, "self": True}] + [
            {"delta": c["delta"], "url": c["url"]} for c in sel["colours"] if not c["selected"]]
        items.sort(key=lambda i: i["delta"])
        offers, excluded, fetched, failed = [], {}, 0, False
        excluded_min = {}   # cheapest price seen among offers we excluded, per reason (shown as context)

        def consider(p):
            ok, why = usable_offer(p, rc)
            if ok:
                offers.append({"colour": p["colour"], "price": p["price"], "battery": p["battery"],
                               "sim": p["sim"], "spec_verified": bool(p["battery"] and p["sim"]), "seller": p["seller"], "ships_from": p["ships_from"],
                               "url": p["url"].replace("http://", "https://").split("#")[0]})
            else:
                excluded[why] = excluded.get(why, 0) + 1
                if why in ("new_battery", "esim_only"):
                    excluded_min[why] = min(excluded_min.get(why, p["price"]), p["price"])
            return ok, why

        for it in items:
            if len(offers) >= rc["verify_colours"] or fetched >= rc["max_page_fetches"]:
                break
            if it.get("self"):
                cur, cur_html = pg, html
            else:
                fetched += 1
                got = self._fetch_page(it["url"], expect)
                if got == "error":
                    failed = True
                    continue
                if got is None:
                    continue
                cur, cur_html = got
            ok, why = consider(cur)
            if not ok and why == "new_battery" and fetched < rc["max_page_fetches"]:
                # the page's buy box is a premium new-battery offer: follow its own "Optimal" option
                alt = next((b for b in parse_selects(cur_html)["batteries"]
                            if "optimal" in b["label"].lower() and not b["selected"]), None)
                if alt:
                    fetched += 1
                    got = self._fetch_page(alt["url"], expect)
                    if got == "error":
                        failed = True
                    elif got:
                        excluded["new_battery"] -= 1  # replaced by its optimal-battery twin
                        excluded_min.pop("new_battery", None)
                        consider(got[0])
        if failed and not offers:
            return "error"
        floor, typical, flags = choose_reference([o["price"] for o in offers], rc)
        ref = floor
        if ref is None and excluded.get("out_of_stock") and not (excluded.get("new_battery") or excluded.get("esim_only")):
            flags = ["ref_out_of_stock"]
        offers.sort(key=lambda o: o["price"])
        if ref is not None:
            if len({o["seller"] for o in offers if o["seller"]}) < 2:
                flags = flags + ["same_seller_only"]
            if any(not o["spec_verified"] for o in offers if o["price"] == ref):
                flags = flags + ["spec_unverified"]
        ref_offer = next((o for o in offers if o["price"] == ref), None)
        return {"price": ref, "floor": floor, "in_stock": ref is not None,
                "url": (ref_offer or {}).get("url") or BASE_URL + path,
                "typical": typical, "supported": supported_price(offers, rc) if offers else None,
                "sellers": len({o["seller"] for o in offers if o["seller"]}), "offers": offers[:3], "excluded": {k: v for k, v in excluded.items() if v},
                "excluded_from": excluded_min if ref is None else {}, "flags": flags}

    def scrape_model(self, slug, wanted):
        """wanted: iterable of (condition, storage). Returns {(cond, storage): result}
        where result is a dict, None (genuinely not offered) or 'error' (fetch failed)."""
        results = {}
        status, html, _ = self.client.get(f"/p/{slug}/")
        if status == 0:
            return {k: "error" for k in wanted}
        if status != 200:
            return {k: None for k in wanted}
        sel = parse_selects(html)
        pg = parse_variant_page(html)
        if sel["selected"] and pg:
            m = _VARIANT_URL_RE.search(pg["url"])
            if m:
                base_path = f"/p/{slug}/{m.group(2)}{m.group(3)}/"
                results[sel["selected"]] = self.evaluate(base_path, pg, html, sel["selected"])

        for cond, storage in wanted:
            if (cond, storage) in results:
                continue
            stem = sel["storages"].get(storage)
            suffix = sel["grades"].get(cond, DEFAULT_SUFFIX.get(cond))
            if stem is None or suffix is None:
                results[(cond, storage)] = None
            else:
                results[(cond, storage)] = self._combo(slug, stem, suffix, (cond, storage))
        return {k: results.get(k) for k in wanted}

    def scrape_many(self, jobs):
        """jobs: {slug: wanted-set}. Models are scraped concurrently."""
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {slug: ex.submit(self.scrape_model, slug, wanted) for slug, wanted in jobs.items()}
            return {slug: f.result() for slug, f in futs.items()}
