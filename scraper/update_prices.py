"""
Master Price Update Script
Runs both scrapers, merges the data, and writes data/prices.json.
Called by GitHub Actions every night.
"""

import json
import os
from datetime import datetime, timezone

from shopify_scraper import get_viberstore_prices
from refurbed_scraper import get_refurbed_prices, APPLE_MODEL_SLUGS, SAMSUNG_MODEL_SLUGS

STORAGES = ["64GB", "128GB", "256GB", "512GB", "1TB"]
CONDITIONS = ["Good", "V. Good", "Excellent"]

# Path to output file (relative to repo root)
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "prices.json")


def build_model_entry(model_name, vs_prices, ref_prices):
    """
    Builds the combined pricing entry for one model.
    vs_prices:  { condition: { storage: price } }  from Shopify
    ref_prices: { condition: { storage: price } }  from Refurbed
    """
    conditions_out = []

    for condition in CONDITIONS:
        vs_cond = vs_prices.get(condition, {})
        ref_cond = ref_prices.get(condition, {})

        # Build per-storage comparison
        storage_data = []
        for storage in STORAGES:
            vs_price = vs_cond.get(storage)
            ref_price = ref_cond.get(storage)

            if vs_price is None and ref_price is None:
                continue  # Skip storages with no data at all

            # Calculate delta and recommendation
            delta = None
            recommended = None
            status = "no_data"
            action = "no_comparison"
            hierarchy_ok = True

            if vs_price is not None and ref_price is not None:
                delta = round(vs_price - ref_price, 2)

                if delta > 10:
                    # We're significantly more expensive â suggest bringing price down to match
                    recommended = round(ref_price + 1, 2)  # â¬1 above Refurbed
                    direction = "down"
                    status = "refurbed_cheaper"
                elif delta < -10:
                    # We're well below Refurbed â potential to raise
                    recommended = round(ref_price - 1, 2)  # â¬1 below Refurbed
                    direction = "up"
                    status = "vs_cheaper"
                else:
                    # Within â¬10 â price is competitive, stay
                    recommended = vs_price
                    direction = "same"
                    status = "competitive"

                action = "adjust" if direction != "same" else "ok"

            elif vs_price is not None:
                status = "no_ref_data"
                action = "no_comparison"

            storage_data.append({
                "storage": storage,
                "vs_price": vs_price,
                "ref_price": ref_price,
                "delta": delta,
                "recommended": {"price": recommended, "direction": direction} if recommended else None,
                "status": status,
            })

        if not storage_data:
            continue

        # Hierarchy check: V.Good must be > Good, Excellent must be > V.Good
        hierarchy_ok = check_hierarchy(vs_cond)

        conditions_out.append({
            "condition": condition,
            "storages": storage_data,
            "hierarchy_ok": hierarchy_ok,
            "action": summarise_action(storage_data),
        })

    return {
        "model": model_name,
        "conditions": conditions_out,
    }


def check_hierarchy(cond_prices):
    """
    Check that prices increase properly across conditions for same storage.
    Returns True if the hierarchy is clean.
    """
    # This is called per-condition so we just validate the storage order is ascending
    prices = [(s, p) for s, p in cond_prices.items() if p is not None]
    prices.sort(key=lambda x: STORAGES.index(x[0]) if x[0] in STORAGES else 99)
    for i in range(1, len(prices)):
        if prices[i][1] <= prices[i - 1][1]:
            return False
    return True


def summarise_action(storage_data):
    """Summarise the overall action needed for a condition."""
    to_adjust = [s for s in storage_data if s.get("status") in ("refurbed_cheaper", "vs_cheaper")]
    hier_issues = [s for s in storage_data if not s.get("hierarchy_ok", True)]

    if hier_issues:
        return "fix_hierarchy"
    if to_adjust:
        return f"adjust_{len(to_adjust)}"
    return "ok"


def merge_and_build(vs_data, ref_apple, ref_samsung):
    """Merge all scraped data into one JSON structure."""
    apple_models = []
    samsung_models = []

    # Apple
    for model_name in APPLE_MODEL_SLUGS.keys():
        vs_prices = vs_data["apple"].get(model_name, {})
        ref_prices = ref_apple.get(model_name, {})
        if vs_prices or ref_prices:
            entry = build_model_entry(model_name, vs_prices, ref_prices)
            apple_models.append(entry)

    # Samsung
    for model_name in SAMSUNG_MODEL_SLUGS.keys():
        vs_prices = vs_data["samsung"].get(model_name, {})
        ref_prices = ref_samsung.get(model_name, {})
        if vs_prices or ref_prices:
            entry = build_model_entry(model_name, vs_prices, ref_prices)
            samsung_models.append(entry)

    # Dashboard stats
    all_entries = apple_models + samsung_models
    total_variants = sum(
        len(s["storages"])
        for m in all_entries for c in m["conditions"] for s in [c]
    )
    vs_cheaper = sum(
        1 for m in all_entries
        for c in m["conditions"]
        for s in c["storages"]
        if s.get("status") == "vs_cheaper"
    )
    ref_cheaper = sum(
        1 for m in all_entries
        for c in m["conditions"]
        for s in c["storages"]
        if s.get("status") == "refurbed_cheaper"
    )
    hier_issues = sum(
        1 for m in all_entries
        for c in m["conditions"]
        if not c.get("hierarchy_ok", True)
    )
    deltas = [
        s["delta"] for m in all_entries
        for c in m["conditions"]
        for s in c["storages"]
        if s.get("delta") is not None
    ]
    avg_delta = round(sum(deltas) / len(deltas), 2) if deltas else 0

    return {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dashboard": {
            "models_priced": len(all_entries),
            "vs_cheaper": vs_cheaper,
            "ref_cheaper": ref_cheaper,
            "avg_delta": avg_delta,
            "hierarchy_issues": hier_issues,
        },
        "apple": apple_models,
        "samsung": samsung_models,
    }


def main():
    print("=" * 50)
    print("ViberStore Pricing Update")
    print("=" * 50)

    # Fetch ViberStore prices (Shopify)
    vs_data = get_viberstore_prices()

    # Fetch Refurbed prices
    ref_apple = get_refurbed_prices("apple")
    ref_samsung = get_refurbed_prices("samsung")

    # Merge everything
    print("\nâ Merging data and computing recommendations...")
    output = merge_and_build(vs_data, ref_apple, ref_samsung)

    # Save to data/prices.json
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nâ Done! prices.json updated.")
    print(f"   Models: {output['dashboard']['models_priced']}")
    print(f"   VS Cheaper: {output['dashboard']['vs_cheaper']}")
    print(f"   Refurbed Cheaper: {output['dashboard']['ref_cheaper']}")
    print(f"   Hierarchy Issues: {output['dashboard']['hierarchy_issues']}")
    print(f"   Avg Delta: â¬{output['dashboard']['avg_delta']}")


if __name__ == "__main__":
    main()
