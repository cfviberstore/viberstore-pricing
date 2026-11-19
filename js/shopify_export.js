/* Safe Shopify price-change export. Pure functions, no DOM, so it can be tested with node.
 *
 * Idea: we never invent a product CSV. You export Products from Shopify right now, drop that file
 * into the tool, and we hand back the SAME rows with only "Variant Price" changed, for products
 * where every approved variant matched by exact SKU and agrees with what the tool saw.
 * Anything that doesn't line up is listed as blocked with a reason and left out.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ShopifyExport = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const REQUIRED = ["Handle", "Title", "Option1 Name", "Option1 Value", "Variant SKU", "Variant Price"];

  function parseCsv(text) {
    text = String(text || "").replace(/^﻿/, "");
    const rows = []; let row = [], cell = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
        else cell += c;
      } else if (c === '"') q = true;
      else if (c === ",") { row.push(cell); cell = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); cell = ""; rows.push(row); row = [];
      } else cell += c;
    }
    if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
    return rows.filter(r => r.length > 1 || (r[0] || "") !== "");
  }

  function toCsv(rows) {
    return rows.map(r => r.map(c => {
      c = String(c == null ? "" : c);
      return /[",\r\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c;
    }).join(",")).join("\r\n");
  }

  const money = x => (Math.round(Number(x) * 100) / 100).toFixed(2);

  /**
   * freshCsv : text of a Shopify "Export products" CSV taken just now
   * approved : [{ key, handle, price, reason, note, variants:[{id, sku, price, options:[o1,o2,o3]}] }]
   *            price = new price approved by a person; variants[].price = the price the tool saw
   * opts     : { maxChangePct } warn (not block) when a change is bigger than this
   * returns  : { ok, fatal, csv, log, applied, blocked, warnings }
   */
  function buildExport(freshCsv, approved, opts) {
    opts = opts || {};
    const maxPct = opts.maxChangePct == null ? 30 : opts.maxChangePct;
    const out = { ok: false, fatal: null, csv: "", log: "", applied: [], blocked: [], warnings: [] };
    const rows = parseCsv(freshCsv);
    if (!rows.length) { out.fatal = "The Shopify export is empty."; return out; }
    const head = rows[0], col = {};
    head.forEach((h, i) => { if (!(h in col)) col[h] = i; });
    const missing = REQUIRED.filter(h => !(h in col));
    if (missing.length) { out.fatal = "This doesn't look like a Shopify product export. Missing columns: " + missing.join(", "); return out; }
    if (!approved || !approved.length) { out.fatal = "Nothing is approved yet."; return out; }

    const body = rows.slice(1);
    const bySku = new Map(), byHandle = new Map();
    body.forEach((r, i) => {
      const sku = (r[col["Variant SKU"]] || "").trim();
      if (sku) { if (!bySku.has(sku)) bySku.set(sku, []); bySku.get(sku).push(i); }
      const h = r[col["Handle"]];
      if (!byHandle.has(h)) byHandle.set(h, []);
      byHandle.get(h).push(i);
    });

    const newPrice = new Map();           // body index -> price string
    const blockedHandles = new Set();     // a product with any blocked variant is left out whole
    const perItem = [];

    for (const item of approved) {
      if (item.blockReason) { out.blocked.push({ key: item.key, handle: item.handle, reason: item.blockReason }); continue; }
      const newP = Number(item.price);
      if (!isFinite(newP) || newP < 1) { out.blocked.push({ key: item.key, reason: "Approved price is not a valid amount." }); continue; }
      const found = [], problems = [];
      for (const v of item.variants || []) {
        if (!v.sku) { problems.push(`Variant ${v.id} has no SKU in Shopify`); continue; }
        const hits = bySku.get(v.sku) || [];
        if (!hits.length) { problems.push(`SKU ${v.sku} is not in the fresh export`); continue; }
        if (hits.length > 1) { problems.push(`SKU ${v.sku} appears ${hits.length} times in the export`); continue; }
        const r = body[hits[0]];
        if (r[col["Handle"]] !== item.handle) { problems.push(`SKU ${v.sku} now belongs to "${r[col["Handle"]]}", not "${item.handle}"`); continue; }
        const now = Number(r[col["Variant Price"]]);
        if (Math.abs(now - Number(v.price)) > 0.005) {
          problems.push(`SKU ${v.sku} costs ${money(now)} in Shopify now but the tool saw ${money(v.price)}; refresh and re-review`); continue;
        }
        const optsNow = [1, 2, 3].map(n => (r[col[`Option${n} Value`]] || "").trim().toLowerCase());
        const optsSeen = (v.options || []).map(x => (x || "").trim().toLowerCase());
        if (optsSeen.some((x, i) => x !== optsNow[i])) { problems.push(`SKU ${v.sku} has different options in Shopify now`); continue; }
        found.push({ idx: hits[0], sku: v.sku, old: now });
      }
      if (!(item.variants || []).length) problems.push("No variants recorded for this item");
      if (problems.length) {
        out.blocked.push({ key: item.key, handle: item.handle, reason: problems.join("; ") });
        blockedHandles.add(item.handle);
        continue;
      }
      for (const f of found) {
        newPrice.set(f.idx, money(newP));
        const pct = Math.abs(newP - f.old) / f.old * 100;
        if (pct > maxPct) out.warnings.push(`${f.sku}: ${pct.toFixed(0)}% change (${money(f.old)} to ${money(newP)})`);
        out.applied.push({ key: item.key, sku: f.sku, handle: item.handle, old: money(f.old), new: money(newP),
                           reason: item.reason || "", note: item.note || "" });
      }
      perItem.push(item.handle);
    }

    // If the same handle appears in two approved items and one is blocked, hold the product back entirely.
    const keep = new Set(perItem.filter(h => !blockedHandles.has(h)));
    if (blockedHandles.size) {
      for (const [i, p] of [...newPrice]) if (blockedHandles.has(body[i][col["Handle"]])) newPrice.delete(i);
      out.applied = out.applied.filter(a => !blockedHandles.has(a.handle));
    }
    if (!out.applied.length) { out.fatal = "None of the approved changes could be matched safely. See the blocked list."; return out; }

    // Output: every row of each affected product, unchanged apart from Variant Price.
    const outRows = [head];
    body.forEach((r, i) => {
      if (!keep.has(r[col["Handle"]])) return;
      const c = r.slice();
      if (newPrice.has(i)) c[col["Variant Price"]] = newPrice.get(i);
      outRows.push(c);
    });
    out.csv = toCsv(outRows);
    out.log = toCsv([["SKU", "Handle", "Old price", "New price", "Reason", "Note"]].concat(
      out.applied.map(a => [a.sku, a.handle, a.old, a.new, a.reason, a.note])));
    out.ok = true;
    return out;
  }

  return { parseCsv, toCsv, buildExport, REQUIRED };
});
