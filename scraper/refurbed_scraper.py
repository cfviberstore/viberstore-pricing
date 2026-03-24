"""
Refurbed.ie Price Scraper
Scrapes refurbed.ie for competitor prices on iPhones and Samsung phones.

How it works:
  - Each model+storage+condition combo has a unique URL on Refurbed
  - URL pattern: /p/{model-slug}/{variantID}{grade}/
      grade suffix: c = Good, b = Very Good, (none) = Excellent
  - We visit the Good URL for each storage, then derive V.Good and Excellent URLs
  - Price is embedded server-side in window.listItem.price2 in the page HTML

No API key required. Uses only requests + BeautifulSoup (no browser needed).
"""

import re
import json
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.refurbed.ie"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-IE,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Refurbed model slugs â maps our model names to Refurbed URL slugs
APPLE_MODEL_SLUGS = {
    "iPhone 17 Pro Max": "iphone-17-pro-max",
    "iPhone 17 Pro": "iphone-17-pro",
    "iPhone 17": "iphone-17",
    "iPhone 16 Pro Max": "iphone-16-pro-max",
    "iPhone 16 Pro": "iphone-16-pro",
    "iPhone 16 Plus": "iphone-16-plus",
    "iPhone 16": "iphone-16",
    "iPhone 15 Pro Max": "iphone-15-pro-max",
    "iPhone 15 Pro": "iphone-15-pro",
    "iPhone 15 Plus": "iphone-15-plus",
    "iPhone 15": "iphone-15",
    "iPhone 14 Pro Max": "iphone-14-pro-max",
    "iPhone 14 Pro": "iphone-14-pro",
    "iPhone 14 Plus": "iphone-14-plus",
    "iPhone 14": "iphone-14",
    "iPhone 13 Pro Max": "iphone-13-pro-max",
    "iPhone 13 Pro": "iphone-13-pro",
    "iPhone 13": "iphone-13",
    "iPhone 12 Pro Max": "iphone-12-pro-max",
    "iPhone 12 Pro": "iphone-12-pro",
    "iPhone 12": "iphone-12",
    "iPhone 12 Mini": "iphone-12-mini",
    "iPhone 11 Pro Max": "iphone-11-pro-max",
    "iPhone 11 Pro": "iphone-11-pro",
    "iPhone 11": "iphone-11",
}

SAMSUNG_MODEL_SLUGS = {
    "Samsung Galaxy S25 Ultra": "samsung-galaxy-s25-ultra",
    "Samsung Galaxy S25+": "samsung-galaxy-s25-plus",
    "Samsung Galaxy S25": "samsung-galaxy-s25",
    "Samsung Galaxy S24 Ultra": "samsung-galaxy-s24-ultra",
    "Samsung Galaxy S24+": "samsung-galaxy-s24-plus",
    "Samsung Galaxy S24": "samsung-galaxy-s24",
    "Samsung Galaxy S24 FE": "samsung-galaxy-s24-fe",
    "Samsung Galaxy S23 Ultra": "samsung-galaxy-s23-ultra",
    "Samsung Galaxy S23+": "samsung-galaxy-s23-plus",
    "Samsung Galaxy S23": "samsung-galaxy-s23",
    "Samsung Galaxy A55": "samsung-galaxy-a55",
    "Samsung Galaxy A35": "samsung-galaxy-a35",
}

# Storage label normalisation (Refurbed uses spaces, we use none)
STORAGE_NORMALISE = {
    "64 GB": "64GB",
    "128 GB": "128GB",
    "256 GB": "256GB",
    "512 GB": "512GB",
    "1000 GB": "1TB",
    "1 TB": "1TB",
    "64GB": "64GB",
    "128GB": "128GB",
    "256GB": "256GB",
    "512GB": "512GB",
    "1TB": "1TB",
}

# Grade suffix mapping
GRADE_SUFFIXES = {
    "Good": "c",
    "V. Good": "b",
    "Excellent": "",   # No suffix = Excellent on Refurbed
}


def fetch_page(path, retries=3):
    """Fetch a Refurbed page and return BeautifulSoup or None."""
    url = BASE_URL + path
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 404:
                return None  # Model/variant doesn't exist on Refurbed
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                print(f"    â  Failed to fetch {path}: {e}")
                return None


def extract_price(soup):
    """
    Extract the best offer price from the page.

    Refurbed's server-rendered HTML consistently embeds two price fields:
      - "price"  : the retail/reference price (~10-15% higher, NOT the market price)
      - "price2" : the actual best offer price from sellers right now  <-- we want this

    IMPORTANT: Do NOT use "price" — it is inflated and will make all Refurbed prices
    appear higher than they really are, causing VS to look artificially cheaper.
    """
    if not soup:
        return None

    # Search the full page HTML for "price2".
    # This field is consistently present in the server-rendered HTML for every
    # variant page (any condition, storage, colour) and always holds the best
    # current offer price across all sellers on Refurbed.
    full_text = str(soup)
    match = re.search(r'"price2"\s*:\s*"([0-9]+(?:\.[0-9]+)?)"', full_text)
    if match:
        try:
            price = float(match.group(1))
            if price > 0:
                return price
        except ValueError:
            pass

    return None


def get_storage_urls(soup, slug):
    """
    From a model page, extract all storage option URLs.
    Returns list of (storage_label, url_path) tuples for the Good grade.
    """
    if not soup:
        return []

    storage_urls = []

    for select in soup.find_all("select"):
        options = select.find_all("option")
        # Identify the storage select by checking if options contain GB/TB
        storage_options = [
            o for o in options
            if re.search(r'\d+\s*(GB|TB)', o.get_text())
        ]
        if len(storage_options) >= 1:
            for opt in storage_options:
                raw_text = opt.get_text(strip=True)
                # Strip delta price like "+â¬65.00"
                storage_label = re.sub(r'\+â¬[\d,.]+.*$', '', raw_text).strip()
                normalised = STORAGE_NORMALISE.get(storage_label)
                url_val = opt.get("value", "")
                # Only include if it's a valid path (not "-1" etc.)
                if normalised and url_val.startswith("/p/"):
                    # Strip query string to get clean path
                    clean_path = url_val.split("?")[0]
                    storage_urls.append((normalised, clean_path))
            break  # Found the storage select, stop looking

    return storage_urls


def derive_condition_path(good_path, condition):
    """
    Given the Good grade path, derive the path for another condition.
    Good:      /p/iphone-16-pro/213891c/   (suffix 'c')
    V. Good:   /p/iphone-16-pro/213891b/   (suffix 'b')
    Excellent: /p/iphone-16-pro/213891/    (no suffix)
    """
    suffix = GRADE_SUFFIXES[condition]

    # Match the variant ID with its grade suffix
    match = re.search(r'(/p/[^/]+/)(\d+)([a-z]*)/$', good_path)
    if not match:
        return None

    prefix = match.group(1)       # /p/iphone-16-pro/
    variant_id = match.group(2)   # 213891

    return f"{prefix}{variant_id}{suffix}/"


def scrape_model(model_name, slug):
    """
    Scrape all prices for one model across all storages and conditions.
    Returns: { condition: { storage: price } }
    """
    print(f"  Scraping: {model_name}")
    prices = {cond: {} for cond in ["Good", "V. Good", "Excellent"]}

    # Step 1: Fetch base page to discover storage options
    base_path = f"/p/{slug}/"
    soup = fetch_page(base_path)
    time.sleep(1)

    if not soup:
        print(f"    â {model_name} not found on Refurbed")
        return prices

    # Step 2: Get all storage URLs (these are for the Good grade)
    storage_urls = get_storage_urls(soup, slug)

    if not storage_urls:
        print(f"    â No storage options found for {model_name}")
        return prices

    print(f"    Found {len(storage_urls)} storage variants: {[s for s, _ in storage_urls]}")

    # Step 3: For each storage, scrape all three conditions
    for storage, good_path in storage_urls:

        for condition in ["Good", "V. Good", "Excellent"]:
            path = derive_condition_path(good_path, condition)
            if not path:
                continue

            page = fetch_page(path)
            time.sleep(0.8)  # Be respectful â don't hammer the server

            if not page:
                # 404 = this condition/storage combo doesn't exist on Refurbed
                continue

            price = extract_price(page)
            if price and price > 0:
                prices[condition][storage] = price
                print(f"    â {storage} / {condition}: â¬{price:.2f}")
            else:
                print(f"    - {storage} / {condition}: no price found")

    return prices


def get_refurbed_prices(brand="apple"):
    """Main entry point â returns all Refurbed prices for a brand."""
    slugs = APPLE_MODEL_SLUGS if brand == "apple" else SAMSUNG_MODEL_SLUGS

    print(f"\nâ Scraping Refurbed ({brand.title()}) prices...")
    all_prices = {}

    for model_name, slug in slugs.items():
        model_prices = scrape_model(model_name, slug)
        all_prices[model_name] = model_prices

    return all_prices


if __name__ == "__main__":
    prices = get_refurbed_prices("apple")
    print(json.dumps(prices, indent=2))
