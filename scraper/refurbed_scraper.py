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
    "Accept-Encoding": "gzip, deflate",
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
                print(f"  DIAG {path}: HTTP 404")
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
    """
    Extract the best offer price using multiple fallback strategies:
    1. data-section="price" HTML element (SSR text, works for all IPs)
    2. data-test="bottom-bar-price" HTML element (SSR text, works for all IPs)
    3. "total" field in gtmData script (works for IE IPs)
    4. "price2" field in gtmData script (fallback)
    """
    if not soup:
        return None

    # Strategy 1: data-section="price" div (SSR-rendered main price element)
    price_section = soup.find(attrs={"data-section": "price"})
    if price_section:
        m = re.search(r'[€\s]([0-9]+\.[0-9]+)', price_section.get_text())
        if m:
            try:
                price = float(m.group(1))
                if price > 0:
                    return price
            except ValueError:
                pass

    # Strategy 2: data-test="bottom-bar-price" element
    bar_price = soup.find(attrs={"data-test": "bottom-bar-price"})
    if bar_price:
        m = re.search(r'[€\s]([0-9]+\.[0-9]+)', bar_price.get_text())
        if m:
            try:
                price = float(m.group(1))
                if price > 0:
                    return price
            except ValueError:
                pass

    # Strategy 3 & 4: gtmData script fallback (IE IPs only)
    full_text = str(soup)
    for pattern in [r'"total"\s*:\s*"([0-9]+(?:\.[0-9]+)?)"',
                    r'"price2"\s*:\s*"([0-9]+(?:\.[0-9]+)?)"']:
        match = re.search(pattern, full_text)
        if match:
            try:
                price = float(match.group(1))
                if price > 0:
                    return price
            except ValueError:
                pass
    return None


def parse_data_price(attr):
    """
    Parse a Refurbed data-price attribute into a signed float delta.
    Format examples: 'less,-€2.01'  ->  -2.01
                     'more,+€15.99' ->  +15.99
                     ''             ->   0.0
    """
    if not attr:
        return 0.0
    m = re.search(r'([+-])[^\d]*([\d]+(?:[.,][\d]+)?)', attr)
    if m:
        sign = 1 if m.group(1) == '+' else -1
        return sign * float(m.group(2).replace(',', '.'))
    return 0.0


def extract_variant_deltas(soup):
    """
    Extract storage and condition price deltas from the variant <select> elements.
    Reads the `data-price` attribute (e.g. 'more,+€220.99', 'less,-€2.01') which
    holds the TRUE delta vs. the absolute cheapest base — NOT the option text, which
    is only populated by JavaScript after page load.

    Also detects 'other' selects (colour, SIM, etc.) and finds the minimum available
    delta for each, so the returned base_adjustment brings the displayed page price
    down to the cheapest possible variant (cheapest colour + cheapest SIM, etc.).

    Returns:
        storage_deltas  dict  e.g. {"256GB": 220.99, "512GB": 262.99, "1TB": 637.99}
        cond_deltas     dict  e.g. {"Good": -2.01, "V. Good": 15.99, "Excellent": 522.99}
        base_adjustment float subtract this from extract_base_price() to get the true
                              base price for the cheapest colour/SIM combination, before
                              adding back a specific storage and condition delta.
    """
    storage_deltas = {}
    cond_deltas = {}
    sel_storage_delta = 0.0
    sel_cond_delta = 0.0
    other_adjustment = 0.0  # sum of (min_delta - sel_delta) for colour/SIM/etc.

    storage_found = False
    cond_found = False

    for select in soup.find_all("select"):
        options = select.find_all("option")
        if not options:
            continue

        # Map every option -> its data-price delta
        opt_deltas = {o: parse_data_price(o.get("data-price", "")) for o in options}

        # Find which option is currently selected in the static HTML
        sel_opt = next((o for o in options if o.get("selected") is not None), None)
        sel_delta = opt_deltas[sel_opt] if sel_opt else 0.0

        # --- Storage select: options that look like "256 GB", "1000 GB", "1 TB" ---
        stor_opts = [o for o in options if re.search(r'\d+\s*(GB|TB)', o.get_text())]
        if stor_opts and not storage_found:
            sel_storage_delta = sel_delta
            for opt in stor_opts:
                label = opt.get_text(strip=True)  # plain text in static HTML, e.g. "256 GB"
                normalised = STORAGE_NORMALISE.get(label)
                if normalised:
                    storage_deltas[normalised] = opt_deltas[opt]
            if storage_deltas:
                storage_found = True
            continue

        # --- Condition select: options that contain "good" or "excellent" ---
        cond_keywords = ["good", "excellent"]
        cond_opts = [o for o in options
                     if any(k in o.get_text().lower() for k in cond_keywords)]
        if cond_opts and not cond_found:
            sel_cond_delta = sel_delta
            for opt in cond_opts:
                text = opt.get_text(strip=True)
                label = re.sub(r'(?i)(most sold|popular|new)', '', text).strip().lower()
                normalised = COND_NORMALISE.get(label)
                if normalised:
                    cond_deltas[normalised] = opt_deltas[opt]
            if cond_deltas:
                cond_found = True
            continue

        # --- Other select (colour, SIM, battery, etc.) ---
        # Pick the cheapest available option to minimise the reference price.
        if opt_deltas:
            min_delta = min(opt_deltas.values())
            other_adjustment += (min_delta - sel_delta)

    # base_adjustment: subtract from displayed price to get the true cheapest base
    # true_base = displayed - sel_storage - sel_cond - sel_other + min_other
    #           = displayed - sel_storage - sel_cond + other_adjustment
    base_adjustment = sel_storage_delta + sel_cond_delta - other_adjustment

    return storage_deltas, cond_deltas, base_adjustment


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

    displayed_price = extract_base_price(soup)
    if not displayed_price:
        print(f"  x {model_name}: no base price found")
        return prices

    storage_deltas, cond_deltas, base_adjustment = extract_variant_deltas(soup)

    if not storage_deltas:
        print(f"  x {model_name}: no storage options found")
        return prices

    if not cond_deltas:
        print(f"  x {model_name}: no condition options found")
        return prices

    true_base = displayed_price - base_adjustment
    print(f"  Displayed: EUR{displayed_price}  TrueBase: EUR{true_base:.2f}  "
          f"Storages: {list(storage_deltas.keys())}  Conditions: {list(cond_deltas.keys())}")

    for cond, c_delta in cond_deltas.items():
        for storage, s_delta in storage_deltas.items():
            price = round(true_base + s_delta + c_delta, 2)
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
