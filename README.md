# ViberStore â Live Pricing Intelligence

Automatically compares ViberStore prices against Refurbed every night.
**100% free.** Built on GitHub + GitHub Actions + GitHub Pages.

---

## How It Works

```
Every night at 2am (automatic)
        â
        â¼
  GitHub Actions runs update_prices.py
        â
        âââ Pulls ViberStore prices from Shopify API  (no key needed)
        âââ Scrapes Refurbed.ie prices by model/storage/condition
        â
        â¼
  Saves updated data/prices.json to the repo
        â
        â¼
  Your team opens the live URL â always fresh data
```

---

## One-Time Setup (takes ~10 minutes)

### Step 1 â Create a GitHub Account
Go to [github.com](https://github.com) and sign up for a free account if you don't have one.

### Step 2 â Create a New Repository
1. Click the **+** icon (top right) â **New repository**
2. Name it: `viberstore-pricing`
3. Set it to **Public** (required for free GitHub Pages)
4. Click **Create repository**

### Step 3 â Upload the Files
1. On your new repository page, click **uploading an existing file**
2. Drag and drop ALL the files from this folder into the upload area
   - Make sure to preserve the folder structure:
     - `scraper/` folder with all Python files
     - `.github/workflows/` folder with the YAML file
     - `data/` folder
     - `index.html`
3. Click **Commit changes**

### Step 4 â Enable GitHub Pages
1. Go to your repository **Settings** tab
2. Click **Pages** in the left sidebar
3. Under **Source**, select **Deploy from a branch**
4. Set branch to **main**, folder to **/ (root)**
5. Click **Save**
6. Wait 1-2 minutes, then your app is live at:
   `https://YOUR-GITHUB-USERNAME.github.io/viberstore-pricing/`

### Step 5 â Run the First Price Update
1. Go to the **Actions** tab in your repository
2. Click **Update Prices Nightly** in the left sidebar
3. Click **Run workflow** â **Run workflow** (green button)
4. Wait ~5-10 minutes for it to complete
5. Refresh your GitHub Pages URL â prices are now live!

After this, it runs automatically every night at 2am. You never need to touch it again.

---

## Manual Price Refresh
Any time you want fresh prices mid-day:
1. Go to **Actions** tab
2. Click **Update Prices Nightly**
3. Click **Run workflow**

---

## File Structure

```
viberstore-pricing/
âââ index.html                          # The pricing app (served by GitHub Pages)
âââ data/
â   âââ prices.json                     # Auto-updated every night by GitHub Actions
âââ scraper/
â   âââ update_prices.py                # Master script â runs both scrapers
â   âââ shopify_scraper.py              # Pulls prices from viberstore.ie (Shopify API)
â   âââ refurbed_scraper.py             # Scrapes refurbed.ie prices
â   âââ requirements.txt               # Python packages needed
âââ .github/
    âââ workflows/
        âââ update_prices.yml           # The automation schedule
```

---

## Adding/Removing Models

To add a new model to the Refurbed scraper, open `scraper/refurbed_scraper.py` and add it to the relevant dictionary:

```python
# For Apple models:
APPLE_MODEL_SLUGS = {
    "iPhone 18 Pro Max": "iphone-18-pro-max",   # â Add new models here
    ...
}

# For Samsung models:
SAMSUNG_MODEL_SLUGS = {
    "Samsung Galaxy S26": "samsung-galaxy-s26",  # â Add new models here
    ...
}
```

The slug is just the Refurbed URL slug â find it by going to the model's page on refurbed.ie and copying the part after `/p/` in the URL.

---

## Troubleshooting

**The GitHub Actions run failed**
â Go to Actions tab, click the failed run, click the job name to see detailed logs.
â Most common cause: Refurbed changed their page structure. Check the scraper logs.

**prices.json is empty or missing**
â Run the workflow manually (Step 5 above). It hasn't run yet.

**The app shows "Could not load pricing data" locally**
â You need to serve it through a web server, not open the HTML file directly.
â Run: `python -m http.server 8000` in the folder, then open `http://localhost:8000`

**A model shows no Refurbed prices**
â That model may not be listed on Refurbed yet (common for very new releases).
â Check manually at refurbed.ie â if it exists, the URL slug may need updating.

---

## Cost

| Service | Cost |
|---|---|
| GitHub (repository) | Free |
| GitHub Actions (automation) | Free (2,000 mins/month â we use ~10 mins/night) |
| GitHub Pages (hosting) | Free |
| **Total** | **â¬0/month** |
