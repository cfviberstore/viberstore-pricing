"""
Master update script: ViberStore (Shopify) + Refurbed -> data/prices.json (+ history).

Usage:  python update_prices.py [--limit N] [--dry-run]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import Policy, build_model, summarise  # noqa: E402
from refurbed_scraper import Refurbed  # noqa: E402
from shopify_scraper import extract_models, fetch_all_products  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CONFIG_PATH = os.path.join(ROOT, "config.json")
PRICES_PATH = os.path.join(ROOT, "data", "prices.json")
HISTORY_PATH = os.path.join(ROOT, "data", "history.json")
SLUGS_PATH = os.path.join(ROOT, "data", "slugs.json")
SCHEMA = 3


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, **kw)
    os.replace(tmp, path)


def variant_key(brand, model, cond, storage):
    return f"{brand}|{model}|{cond}|{storage}"


def update_history(models, now, keep_days):
    history = load_json(HISTORY_PATH, {"entries": []})
    snap = {}
    for m in models:
        for v in m["variants"]:
            if v["vs_price"] is None and v["ref_price"] is None:
                continue
            snap[variant_key(m["brand"], m["model"], v["condition"], v["storage"])] = [v["vs_price"], v["ref_price"]]
    day = now.strftime("%Y-%m-%d")
    entries = [e for e in history["entries"] if e["date"] != day]
    entries.append({"date": day, "prices": snap})
    history["entries"] = sorted(entries, key=lambda e: e["date"])[-keep_days:]
    history["format"] = "prices[key] = [viberstore_price, refurbed_price]"
    save_json(HISTORY_PATH, history, separators=(",", ":"))
    return len(history["entries"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only process the first N models (testing)")
    ap.add_argument("--dry-run", action="store_true", help="don't write any files")
    args = ap.parse_args()

    t0 = time.time()
    cfg = load_json(CONFIG_PATH, {})
    policy = Policy.from_config(cfg.get("policy"))
    now = datetime.now(timezone.utc)

    print("ViberStore: fetching catalogue ...")
    products = fetch_all_products(cfg.get("store_url", "https://viberstore.ie"))
    vs_models, skipped = extract_models(products, cfg.get("brands", ["Apple", "Samsung"]), cfg.get("exclude_models", []))
    titles = sorted(vs_models)[: args.limit] if args.limit else sorted(vs_models)
    print(f"  {len(vs_models)} models; processing {len(titles)}. "
          f"Ignored 'New Battery' variants: {skipped['new_battery_variants']}; unparsed: {skipped['unparsed_models']}")

    ref = Refurbed(rc=cfg.get("refurbed"))
    slug_cache = load_json(SLUGS_PATH, {})
    overrides = cfg.get("slug_overrides", {})

    print("Refurbed: matching models ...")
    slugs = {}
    for t in titles:
        slugs[t] = ref.resolve_slug(t, slug_cache, overrides)
    unmatched = [t for t, s in slugs.items() if not s]
    print(f"  matched {len(titles) - len(unmatched)}/{len(titles)}; no Refurbed page: {unmatched}")

    print("Refurbed: fetching exact variant prices ...")
    jobs = {}
    for t, slug in slugs.items():
        if slug:
            jobs.setdefault(slug, set()).update(vs_models[t]["combos"].keys())
    scraped = ref.scrape_many(jobs)

    previous = load_json(PRICES_PATH, {})
    prev_ref = {}
    for m in previous.get("models", []):
        for v in m["variants"]:
            if v.get("ref_price") is not None:
                prev_ref[variant_key(m["brand"], m["model"], v["condition"], v["storage"])] = v

    models_out, carried = [], 0
    for t in titles:
        vm = vs_models[t]
        brand = "samsung" if vm["vendor"].lower() == "samsung" else vm["vendor"].lower()
        slug = slugs[t]
        ref_prices = dict(scraped.get(slug, {})) if slug else {}
        # transient fetch failure: keep yesterday's number, clearly marked as stale
        for key, val in list(ref_prices.items()):
            if val == "error":
                old = prev_ref.get(variant_key(brand, t, *key))
                if old:
                    ref_prices[key] = {"price": old["ref_price"], "in_stock": old.get("ref_in_stock", True),
                                       "url": old.get("ref_url"), "flags": ["stale_data"],
                                       "checked": old.get("ref_checked") or previous.get("last_updated")}
                    carried += 1
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        for val in ref_prices.values():
            if isinstance(val, dict):
                val.setdefault("checked", stamp)
        variants = build_model(vm["combos"], ref_prices, policy)
        for v in variants:
            if (v["condition"], v["storage"]) in vm["combos"]:
                v["shop_variants"] = [
                    {"id": c["id"], "sku": c.get("sku", ""), "colour": c["colour"], "price": c["price"], "in_stock": c["available"],
                     "options": c["options"]}
                    for c in vm["combos"][(v["condition"], v["storage"])]["colours"]
                ]
        models_out.append({
            "brand": brand, "model": t, "handle": vm["handle"], "refurbed_slug": slug,
            "variants": variants,
        })

    summary = summarise(models_out)
    output = {
        "schema": SCHEMA,
        "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy": cfg.get("policy", {}),
        "summary": summary,
        "unmatched_models": unmatched,
        "extensions": {"cost_data": False, "stock_qty": False, "new_battery_benchmark": False},
        "models": models_out,
    }

    # ---- guard: never publish a clearly broken scrape ------------------------------------
    prev_n = sum(1 for m in previous.get("models", []) for v in m["variants"] if v.get("ref_price") is not None)
    new_n = sum(1 for m in models_out for v in m["variants"] if v["ref_price"] is not None)
    min_cov = cfg.get("min_coverage_vs_previous", 0.6)
    if not args.limit and prev_n >= 20 and new_n < prev_n * min_cov:
        print(f"ABORT: only {new_n} Refurbed prices vs {prev_n} last time (< {min_cov:.0%}). "
              f"Keeping the previous prices.json.")
        sys.exit(1)

    print(f"Done in {time.time() - t0:.0f}s - {summary['variants']} variants, "
          f"{summary['comparable']} compared, {summary['actions']} recommended changes, "
          f"{carried} stale carried over, {ref.client.errors} request failures.")
    if args.dry_run:
        return
    save_json(PRICES_PATH, output, indent=1)
    save_json(SLUGS_PATH, slug_cache, indent=1, sort_keys=True)
    print(f"History: {update_history(models_out, now, cfg.get('history_days', 120))} day(s) stored")


if __name__ == "__main__":
    main()
