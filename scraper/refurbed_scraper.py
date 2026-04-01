"""
Refurbed.ie Price Scraper — Delta Approach

How it works:
  - Fetches ONE base page per model (e.g. /p/iphone-15-pro-max/)
  - Extracts the base price from the server-rendered gtmData (price2 field)
  - Extracts storage and condition price deltas from the variant select dropdowns
  - Computes all variant prices: base_price + storage_delta + condition_delta

This approach is resilient to Refurbed's server-side rendering changes and
requires only 1 HTTP request per model instead of 9+.
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
    "Accept-Encoding": "gzip, deflate, br",
}

# Refurbed model slugs
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

STORAGE_NORMALISE = {
    "64 GB": "64GB", "128 GB": "128GB", "256 GB": "256GB",
    "512 GB": "512GB", "1000 GB": "1TB", "1 TB": "1TB",
    "64GB": "64GB", "128GB": "128GB", "256GB": "256GB",
    "512GB": "512GB", "1TB": "1TB",
}

# Refurbed condition label (lowercase) -> our internal label
COND_NORMALISE = {
    "good": "Good",
    "very good": "V. Good",
    "excellent": "Excellent",
    # "premium" intentionally excluded — not a ViberStore grade
}


def fetch_page(path, retries=3):
    url = BASE_URL + path
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  x Failed to fetch {path}: {e}")
                return None


def extract_base_price(soup):
    """Extract price2 (best offer price) from the server-rendered gtmData script."""
    if not soup:
        return None
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


def extract_storage_deltas(soup):
    """
    Extract storage options and their price deltas from the storage select.
    Returns dict: {"256GB": 0.0, "512GB": 50.0, "1TB": 83.0}
    The option with no +delta is the base (delta=0).
    """
    deltas = {}
    for select in soup.find_all("select"):
        options = select.find_all("option")
        stor_opts = [o for o in options if re.search(r'\d+\s*(GB|TB)', o.get_text())]
        if not stor_opts:
            continue
        for opt in stor_opts:
            text = opt.get_text(strip=True)
            delta_match = re.search(r'\+[^\d]*([\d]+(?:[.,][\d]+)?)', text)
            delta = float(delta_match.group(1).replace(',', '.')) if delta_match else 0.0
            label = re.sub(r'\+.*$', '', text).strip()
            normalised = STORAGE_NORMALISE.get(label)
            if normalised:
                deltas[normalised] = delta
        if deltas:
            break  # found the storage select
    return deltas


def extract_condition_deltas(soup):
    """
    Extract condition options and their price deltas from the condition/appearance select.
    Returns dict: {"Good": 0.0, "V. Good": 36.0, "Excellent": 90.0}
    Skips Premium and battery/color selects.
    """
    deltas = {}
    for select in soup.find_all("select"):
        options = select.find_all("option")
        cond_keywords = ["good", "excellent"]
        cond_opts = [o for o in options if any(k in o.get_text().lower() for k in cond_keywords)]
        if not cond_opts:
            continue
        for opt in cond_opts:
            text = opt.get_text(strip=True)
            delta_match = re.search(r'\+[^\d]*([\d]+(?:[.,][\d]+)?)', text)
            delta = float(delta_match.group(1).replace(',', '.')) if delta_match else 0.0
            # Strip delta and extra labels like "Most sold", "Popular"
            label = re.sub(r'\+.*$', '', text).strip()
            label = re.sub(r'(?i)(most sold|popular|new)', '', label).strip().lower()
            normalised = COND_NORMALISE.get(label)
            if normalised:
                deltas[normalised] = delta
        if deltas:
            break  # found the condition select
    return deltas


def scrape_model(model_name, slug):
    """
    Scrape all prices for one model using the delta approach.
    One HTTP request per model instead of one per variant.
    """
    print(f"  Scraping: {model_name}")
    prices = {cond: {} for cond in ["Good", "V. Good", "Excellent"]}

    soup = fetch_page(f"/p/{slug}/")
    time.sleep(1)

    if not soup:
        print(f"  x {model_name} not found on Refurbed")
        return prices

    base_price = extract_base_price(soup)
    if not base_price:
        print(f"  x {model_name}: no base price found")
        return prices

    storage_deltas = extract_storage_deltas(soup)
    if not storage_deltas:
        print(f"  x {model_name}: no storage options found")
        return prices

    cond_deltas = extract_condition_deltas(soup)
    if not cond_deltas:
        print(f"  x {model_name}: no condition options found")
        return prices

    print(f"  Base: EUR{base_price}  Storages: {list(storage_deltas.keys())}  Conditions: {list(cond_deltas.keys())}")

    for cond, c_delta in cond_deltas.items():
        for storage, s_delta in storage_deltas.items():
            price = round(base_price + s_delta + c_delta, 2)
            prices[cond][storage] = price
            print(f"  + {storage} / {cond}: EUR{price:.2f}")

    return prices


def get_refurbed_prices(brand="apple"):
    slugs = APPLE_MODEL_SLUGS if brand == "apple" else SAMSUNG_MODEL_SLUGS
    print(f"\nScraping Refurbed ({brand.title()}) prices (delta method)...")
    all_prices = {}
    for model_name, slug in slugs.items():
        all_prices[model_name] = scrape_model(model_name, slug)
    return all_prices


if __name__ == "__main__":
    prices = get_refurbed_prices("apple")
    print(json.dumps(prices, indent=2))
