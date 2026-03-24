"""
ViberStore Shopify Price Scraper
Fetches all product prices from the public Shopify products.json endpoint.
No API key required â this is a public endpoint on all Shopify stores.
"""

import requests
import time

STORE_URL = "https://viberstore.ie"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ViberStorePricingBot/1.0)"}

# Conditions we care about â must match Shopify option2 values exactly
TARGET_CONDITIONS = {"Good", "V. Good", "Very Good", "Excellent"}

# Storage sizes we track
TARGET_STORAGES = {"64GB", "128GB", "256GB", "512GB", "1TB"}

# Phone brands to include
APPLE_KEYWORDS = ["iphone"]
SAMSUNG_KEYWORDS = ["samsung galaxy", "samsung s", "samsung a"]


def fetch_all_products():
    """Pages through the Shopify products.json endpoint and returns all products."""
    all_products = []
    page = 1

    while True:
        url = f"{STORE_URL}/products.json?limit=250&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            products = resp.json().get("products", [])
        except Exception as e:
            print(f"  â  Error fetching page {page}: {e}")
            break

        if not products:
            break

        all_products.extend(products)
        print(f"  Shopify page {page}: {len(products)} products fetched")

        if len(products) < 250:
            break

        page += 1
        time.sleep(0.5)

    return all_products


def extract_phone_prices(products, keywords):
    """
    From the full product list, extracts phones matching keywords.
    Returns a dict: { model_title: { condition: { storage: price } } }
    """
    result = {}

    for product in products:
        title = product.get("title", "")
        product_type = product.get("product_type", "")

        # Only include mobile phones matching our keywords
        title_lower = title.lower()
        if product_type != "Mobile Phones":
            continue
        if not any(kw in title_lower for kw in keywords):
            continue

        # Get option positions (Shopify options can be in any order)
        options = {o["name"]: idx + 1 for idx, o in enumerate(product.get("options", []))}
        mem_key = f"option{options.get('Memory', 1)}"
        cond_key = f"option{options.get('Condition', 2)}"

        model_prices = {}

        for variant in product.get("variants", []):
            storage = variant.get(mem_key, "")
            condition = variant.get(cond_key, "")
        # Normalize condition names to a single canonical form
        if condition == "Very Good":
            condition = "V. Good"
            price = float(variant.get("price", 0))
            available = variant.get("available", False)

            if condition not in TARGET_CONDITIONS:
                continue
            if storage not in TARGET_STORAGES:
                continue
            if price == 0:
                continue

            if condition not in model_prices:
                model_prices[condition] = {}

            # If multiple colours exist for same storage+condition, keep the lowest price
            if storage not in model_prices[condition] or price < model_prices[condition][storage]:
                model_prices[condition][storage] = price

        if model_prices:
            result[title] = model_prices

    return result


def get_viberstore_prices():
    """Main entry point â returns structured prices for Apple and Samsung."""
    print("â Fetching ViberStore (Shopify) prices...")
    products = fetch_all_products()
    print(f"  Total products found: {len(products)}")

    apple_prices = extract_phone_prices(products, APPLE_KEYWORDS)
    samsung_prices = extract_phone_prices(products, SAMSUNG_KEYWORDS)

    print(f"  Apple models: {len(apple_prices)}")
    print(f"  Samsung models: {len(samsung_prices)}")

    return {"apple": apple_prices, "samsung": samsung_prices}


if __name__ == "__main__":
    import json
    prices = get_viberstore_prices()
    print(json.dumps(prices, indent=2))
