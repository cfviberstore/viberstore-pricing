# ViberStore pricing desk

Compares every ViberStore phone price (Apple + Samsung) with the **exact** Refurbed.ie price for the same
model, storage and condition, and suggests what to change. Free: GitHub Actions + GitHub Pages.

```
twice a day (GitHub Actions)
  scraper/update_prices.py
    ├─ ViberStore:  viberstore.ie/products.json  (in-stock colours only)
    ├─ Refurbed:    one real page per storage × condition, plus a few colour checks
    ├─ engine.py:   gaps, suggestions, price-ladder rules, data-quality flags
    └─ writes data/prices.json, data/history.json
  index.html reads those files  →  your team opens the Pages URL
```

## Using the dashboard
The daily routine is: open the page, work down **Today's queue**, approve or skip, then export from the **Review basket**.
* **Today's queue** – in-stock variants only, biggest and best-evidenced first. Each row shows your price, Refurbed's
  cheapest comparable price, the *supported* price (what two different sellers back), the suggestion, a **kind**
  (Market / Capped / Ladder / Needs review), a **confidence** level with what is missing, and a plain-English reason.
  *Approve* accepts the suggestion, *Edit* lets you set your own price, *Skip* rejects it. Edit and Skip ask for a
  reason, which is recorded. *Needs review* rows have no Approve button: you choose the price yourself.
* **Review basket** – everything you approved or skipped. Build the Shopify price file here (see below) and download
  the decision log.
* **Maintenance** – sold-out variants. Same suggestions, kept out of the daily count so they don't drown real work.
* **All models** – storage × condition grid per model. **Market position** – where you sit overall.
* **Details** on any row opens the offer evidence (sellers, battery, SIM, colour) and the price history.

Decisions are saved in your browser only. If a variant's Shopify price changes, its old decision is dropped.

## Getting prices into Shopify (safe export)
The tool never changes Shopify. It builds a file you import yourself, and only after checking it against a fresh export.
1. Shopify admin → Products → Export → All products → CSV. Do this right before you export from the tool.
2. In the Review basket, choose that file and press *Check and build price file*.
3. Each approved variant is matched by **exact SKU**, then checked: same product handle, same options, and the price
   in the fresh export must equal the price the tool saw. If any colour of a product fails, the whole product is left
   out and the reason is shown.
4. Download the price file and import it in Shopify (Products → Import → tick *Overwrite existing products*). It
   contains every row of each affected product exactly as exported, with only `Variant Price` changed, so titles,
   options, images and everything else are preserved. A change log CSV lists old and new prices.

Variants with no SKU (or the same SKU twice) can't be exported safely and are listed as blocked. Fixing those SKUs in
Shopify unblocks them.

## What the labels mean
| kind | meaning |
|---|---|
| Market | Moves toward Refurbed's cheapest comparable price |
| Capped | The market target is further away, but one step is limited by `max_raise_pct` / `max_lower_pct` |
| Ladder | Keeps Good < V. Good < Excellent and small < large storage. Obeys the same raise limit and never goes above Refurbed's price plus tolerance |
| Needs review | The tool will not pick for you: ladder needs more than the limits allow, the gap is over 35%, or Refurbed's own prices look inconsistent |

| confidence | meaning |
|---|---|
| High | Two or more independent sellers, battery and SIM verified, fresh data, no oddities |
| Medium | A gap such as one seller only, a lone cheap offer, or missing battery/SIM details |
| Low | Stale data, only one offer found, inconsistent Refurbed grades, or an unusually large gap |

We only see the buy-box seller for each colour, so "independent sellers" is a sample, not a full census.

## Costs and margins (Costs tab)
Upload Brian's price book (.xlsx) in the **Costs** tab. The **VSG Cost** column (confirmed as the final euro cost per
phone, including the NZ handling fee) is used.

**Privacy: cost prices are never stored in this repo.** The dashboard is a public website, so the file is read inside
your browser and kept only in that browser's storage. Upload it again on each computer. Never commit the price book
or any file with cost prices to the repo.

* **Zero base prices are ignored.** The sheet still shows a €5 "cost" for them (the handling fee alone). They are
  treated as having no cost.
* **Matching is exact or confirmed.** A shop variant is matched only when name and storage are identical. Near misses
  (5G vs 4G, "Z Flip", several rows fit) are listed for you to confirm one by one; nothing is guessed. Confirmed
  matches are saved in the browser, with a download/restore button as a backup.
* **Margin** = (price ex-VAT − cost) ÷ price ex-VAT. Settings: VAT rate (23%), target 20%, floor 15%, hard warning 10%.
  Every queue row shows margin and profit per phone before and after the change; Details shows the lowest price that
  still keeps the floor.
* **Protection.** Default is "send low-margin changes to review". A price that would sell at a loss always goes to
  *Needs review*; in that mode so does any change under the 10% hard warning, and any cut under the 15% floor. Raises
  that improve a thin margin are never held. **An old or unverified cost sheet only adds warnings** ("old cost sheet",
  "estimate"); it never switches a rule off.
* **Every approved price is checked, including prices you type.** Editing a price shows its margin as you type. A
  below-cost price needs you to tick "Sell below cost anyway", and without that it is left out of the Shopify export.
  Margin is re-checked against today's costs at export time and written to the change log.
* **Approvals expire when the evidence changes.** An approval records the Refurbed price it was based on. If that
  price moves by more than €5 (or appears or disappears), or the approval is over 48 hours old, it is marked "Needs
  re-review", goes back into Today's queue and will not export until you approve it again. A "sell below cost anyway"
  tick only holds for the cost and VAT setting it was given under; if either changes you must confirm again.
* **Margin is a floor, not a target.** The 20% target is a guide to profitability. The tool never lowers a price to
  reach it; suggestions still follow Refurbed, and the margin rules only hold back changes that go too low.
* **Estimates.** Margins say "Est." until you tick both "Brian confirmed one cost applies to every grade" and "VAT
  treatment confirmed".
* **Storage of the price book.** Default is session only (this tab; gone when you close it). Tick "Remember on this
  computer" to keep it in this browser. Note that browser storage is shared by every page on the same website address,
  and anyone using this computer profile can read it. Cost figures are never in the public data files.
* **Safe matching.** A confirmed match must have the same storage size as the shop variant, otherwise it is ignored
  and shown as rejected. Confirmed matches are kept with the price-book row, storage and date.
* **Not yet confirmed:** whether VSG Cost excludes VAT; whether one cost really applies to Good, V. Good and
  Excellent; and which VAT scheme applies (standard VAT on the full price is assumed, the second-hand margin scheme is
  not supported). Fees, delivery and returns are not included in margin.

## Planned: stock quantities and new-battery benchmarks
`engine.build_model(..., guards=[...])` accepts guard functions for further checks. Stock quantities and a separate
new-battery benchmark plug in there without changing the pricing rules.

## How the Refurbed price is chosen
Refurbed is many sellers fighting for the buy box, so the headline price is often not comparable to yours. For each
storage × condition we collect the offers and keep only **comparable** ones:
* in stock
* **not a new-battery** offer (you sell the standard battery)
* **not eSIM-only** (your site doesn't do eSIM)
* **any colour** (the cheapest colour counts, since you price colours the same)

The **floor** (cheapest comparable offer) is the market price used for your gap. If the floor is a lone cheap offer
(next offer is more than max(€15, 6%) higher) it is flagged, and *cuts* aim at the next, corroborated price so you
don't chase one seller; *raises* never go above the floor. Variants that only have new-battery offers are shown as
context and flagged "no comparable offer". The History drawer lists the offers behind each price. The all-offers
page (`/o/`) is disallowed by Refurbed's robots.txt and is never requested. A full run takes about 6 minutes.

## Pricing rules — `config.json → policy`
| setting | default | meaning |
|---|---|---|
| `tolerance_eur` | 10 | within ± this of Refurbed = competitive, no change |
| `undercut_eur` | 0 | when moving, aim this far under Refurbed. 0 matches it: the highest X9.99 at or below their price (€499.99 → €499.99, €505 → €499.99, €512 → €509.99). Use 5 or 10 to undercut |
| `max_raise_pct` / `max_lower_pct` | 10 / 30 | biggest single step either way |
| `min_condition_gap_eur` | 10 | Good < V. Good < Excellent by at least this |
| `min_storage_gap_eur` | 20 | smaller storage cheaper than larger by at least this |
| `review_gap_pct` | 35 | bigger gaps are flagged "check" |

Other settings: `brands`, `exclude_models`, `slug_overrides` (force a Refurbed URL name for a model),
`history_days`, `min_coverage_vs_previous` (safety net – see below).

## Setup (10 minutes)
1. Create a **public** repo, upload this folder (keep the structure, including `.github/`).
2. Settings → Pages → Deploy from branch → `main` / root.
3. Settings → Actions → General → Workflow permissions → **Read and write** (the workflow also requests it).
4. Actions → *Update prices* → Run workflow. Open your Pages URL when it finishes (~6 min).

## Safety nets
* Tests run before every update; if they fail nothing is published.
* If fewer than 60 % of last run's Refurbed prices are found, the run fails and the old data stays live.
* A Refurbed page that redirects to another configuration, or whose own selected grade/storage differ from what
  was requested, is discarded rather than trusted.
* Model matching checks Refurbed's product *name*, so "S24" can never silently map to "S24 FE".
* A temporary fetch failure keeps the last known Refurbed price and marks it.

## Why v1 numbers were wrong
v1 rebuilt Refurbed prices by adding "+€x" dropdown differences from a single page. Those differences are relative
to the page's current selection and don't add up across storage and grade, so large storages were off by €30–€500
(e.g. iPhone 13 Pro 1TB Good stored €1,024.99, real €484.99). It also priced sold-out colours and silently skipped
models whose names differ slightly between the two sites (A55 5G, S24 Plus…). v2 reads real per-variant prices.

## Run locally
```
pip install -r scraper/requirements.txt
python -m unittest discover -s tests          # also runs the Node export tests if node is installed
python scraper/update_prices.py            # --limit 5 --dry-run for a quick test
python -m http.server 8000                 # then open http://localhost:8000
```

## Notes
Refurbed offers can move within minutes, so differences of a few euros between a scrape and a manual check are
normal – that's what the ±€10 band absorbs. Be considerate with scraping: the updater is rate-limited and only
fetches the combinations you actually sell. Check Refurbed's terms before using the data commercially.
