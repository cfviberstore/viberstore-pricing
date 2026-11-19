"""
ViberStore (Shopify) scraper (v2).

Reads the public /products.json feed and returns, per phone model, one record per
(condition, storage) combination:

    price      lowest price among IN-STOCK colours (falls back to lowest overall,
               with in_stock=False, when every colour is sold out)
    in_stock   whether at least one colour can actually be bought
    colours    every colour variant {id, colour, price, available} so price changes
               can be applied to all of them (exporter uses this)

v1 problems fixed here: sold-out colours set the "lowest price"; models were picked
by title keyword and matched to Refurbed by exact title (so "Galaxy A55 5G" or
"S24 Plus" were silently dropped); option columns with odd names ("Condition ") were
mis-read; the "Very Good - New Battery" grade was ignored without being reported.
"""
import json
import re

from http_client import Client

CONDITION_MAP = {"good": "Good", "very good": "V. Good", "v. good": "V. Good", "excellent": "Excellent"}
_STORAGE_RE = re.compile(r"^\s*(\d+)\s*(GB|TB)\s*$", re.I)


def normalise_condition(v):
    return CONDITION_MAP.get((v or "").strip().lower())


def normalise_storage(v):
    m = _STORAGE_RE.match(v or "")
    return f"{int(m.group(1))}{m.group(2).upper()}" if m else None


def clean_title(title):
    return re.sub(r"^Apple\s+", "", title.strip())


def fetch_all_products(store_url):
    client = Client(store_url, headers={"User-Agent": "Mozilla/5.0 (compatible; ViberStorePricingBot/2.0)"}, interval=0.4)
    products, page = [], 1
    while True:
        status, text, _ = client.get(f"/products.json?limit=250&page={page}")
        if status != 200:
            raise RuntimeError(f"Shopify products.json page {page} failed (HTTP {status})")
        batch = json.loads(text).get("products", [])
        products.extend(batch)
        if len(batch) < 250:
            return products
        page += 1


def _option_positions(product):
    """{'memory': 1, 'condition': 2, ...} - tolerant of case / spacing / wording."""
    pos = {}
    for i, o in enumerate(product.get("options", []), start=1):
        name = (o.get("name") or "").strip().lower()
        if name in ("memory", "storage", "capacity"):
            pos["memory"] = i
        elif "condition" in name or name == "grade":
            pos["condition"] = i
        elif name in ("colour", "color"):
            pos["colour"] = i
    return pos


def extract_models(products, brands, exclude=()):
    """Returns ({title: model_record}, skipped_counts)."""
    brands = {b.lower() for b in brands}
    exclude = {e.lower() for e in exclude}
    models, skipped = {}, {"new_battery_variants": 0, "unparsed_models": []}

    for p in products:
        if p.get("product_type") != "Mobile Phones":
            continue
        if (p.get("vendor") or "").lower() not in brands:
            continue
        title = clean_title(p.get("title", ""))
        if title.lower() in exclude:
            continue
        pos = _option_positions(p)
        if "memory" not in pos or "condition" not in pos:
            skipped["unparsed_models"].append(title)
            continue

        combos = {}
        for v in p.get("variants", []):
            raw_cond = v.get(f"option{pos['condition']}")
            cond = normalise_condition(raw_cond)
            if cond is None:
                if raw_cond and "new battery" in raw_cond.lower():
                    skipped["new_battery_variants"] += 1
                continue
            storage = normalise_storage(v.get(f"option{pos['memory']}"))
            try:
                price = round(float(v.get("price") or 0), 2)
            except ValueError:
                price = 0
            if storage is None or price <= 0:
                continue
            colour = v.get(f"option{pos['colour']}") if "colour" in pos else None
            combos.setdefault((cond, storage), []).append(
                {"id": v.get("id"), "sku": (v.get("sku") or "").strip(), "colour": colour, "price": price, "available": bool(v.get("available")),
                 "raw_memory": v.get(f"option{pos['memory']}"), "raw_condition": raw_cond,
                 "options": [v.get("option1"), v.get("option2"), v.get("option3")]}
            )

        if not combos:
            continue
        out = {}
        for key, variants in combos.items():
            in_stock_variants = [x for x in variants if x["available"]]
            pool = in_stock_variants or variants
            out[key] = {
                "price": min(x["price"] for x in pool),
                "in_stock": bool(in_stock_variants),
                "price_spread": round(max(x["price"] for x in pool) - min(x["price"] for x in pool), 2),
                "colours": variants,
            }
        models[title] = {
            "title": title,
            "vendor": p.get("vendor"),
            "handle": p.get("handle"),
            "url": f"/products/{p.get('handle')}",
            "combos": out,
        }
    return models, skipped
