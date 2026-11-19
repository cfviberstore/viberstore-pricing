/* Cost data: read Brian's price book (.xlsx) in the browser, match it to shop variants,
 * and work out margins. Pure functions (plus one async xlsx reader) so node can test them.
 *
 * Why the browser? The dashboard is published on a public GitHub Pages site. Cost prices must never
 * be committed to that repo, so the file is read here and kept only in this browser's storage.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Costs = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* ---------------- minimal .xlsx reader ----------------------------------------------------- */
  const dec = new TextDecoder("utf-8");
  const u16 = (b, o) => b[o] | (b[o + 1] << 8);
  const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;

  async function inflateRaw(bytes) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  function zipEntries(buf) {
    const b = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    let e = -1;
    for (let i = b.length - 22; i >= Math.max(0, b.length - 70000); i--) if (u32(b, i) === 0x06054b50) { e = i; break; }
    if (e < 0) throw new Error("This doesn't look like an Excel (.xlsx) file.");
    const n = u16(b, e + 10); let p = u32(b, e + 16); const out = new Map();
    for (let i = 0; i < n; i++) {
      if (u32(b, p) !== 0x02014b50) break;
      const method = u16(b, p + 10), csize = u32(b, p + 20), nl = u16(b, p + 28), el = u16(b, p + 30), cl = u16(b, p + 32), lo = u32(b, p + 42);
      const name = dec.decode(b.subarray(p + 46, p + 46 + nl));
      out.set(name, async () => {
        const ln = u16(b, lo + 26), le = u16(b, lo + 28), start = lo + 30 + ln + le;
        const data = b.subarray(start, start + csize);
        return method === 0 ? data : inflateRaw(data);
      });
      p += 46 + nl + el + cl;
    }
    return out;
  }

  const unesc = s => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&amp;/g, "&");
  const colIndex = ref => { let n = 0; for (const ch of ref.replace(/[0-9]/g, "")) n = n * 26 + ch.charCodeAt(0) - 64; return n - 1; };

  /** returns { sheetName, rows: [[cell,...],...], savedAt: Date|null, formulaCells, uncachedFormulas } for the first sheet */
  async function readXlsx(buf) {
    const z = zipEntries(buf), text = async n => z.has(n) ? dec.decode(await z.get(n)()) : null;
    const wb = await text("xl/workbook.xml");
    if (!wb) throw new Error("No workbook found inside this file.");
    const sh = /<sheet\b[^>]*name="([^"]*)"[^>]*r:id="([^"]*)"/.exec(wb);
    if (!sh) throw new Error("The workbook has no sheets.");
    const rels = (await text("xl/_rels/workbook.xml.rels")) || "";
    let target = null;
    for (const m of rels.matchAll(/<Relationship\b[^>]*>/g)) {
      if (new RegExp(`Id="${sh[2]}"`).test(m[0])) target = (/Target="([^"]*)"/.exec(m[0]) || [])[1];
    }
    if (!target) throw new Error("Could not find the first sheet's data.");
    const path = target.startsWith("/") ? target.slice(1) : "xl/" + target.replace(/^\.\//, "");
    const sheet = await text(path);
    if (!sheet) throw new Error("Could not read the first sheet.");
    const sst = await text("xl/sharedStrings.xml"), strings = [];
    if (sst) for (const si of sst.matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>/g))
      strings.push(unesc([...si[1].matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map(x => x[1]).join("")));
    const rows = []; let formulaCells = 0, uncachedFormulas = 0;
    for (const rm of sheet.matchAll(/<row\b[^>]*?\br="(\d+)"[^>]*>([\s\S]*?)<\/row>/g)) {
      const r = +rm[1] - 1, row = rows[r] = [];
      for (const cm of rm[2].matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
        const attrs = cm[1], inner = cm[2] || "";
        const ref = (/\br="([A-Z]+\d+)"/.exec(attrs) || [])[1]; if (!ref) continue;
        const t = (/\bt="([^"]*)"/.exec(attrs) || [])[1];
        const v = (/<v>([\s\S]*?)<\/v>/.exec(inner) || [])[1];
        const hasF = /<f\b/.test(inner);
        let val = null;
        if (t === "s" && v != null) val = strings[+v] ?? null;
        else if (t === "inlineStr") val = unesc([...inner.matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>/g)].map(x => x[1]).join(""));
        else if (t === "str" && v != null) val = unesc(v);
        else if (t === "b") val = v === "1";
        else if (v != null && v !== "") val = Number(v);
        if (hasF) { formulaCells++; if (v == null || v === "") uncachedFormulas++; }
        row[colIndex(ref)] = val;
      }
    }
    const core = await text("docProps/core.xml");
    const mod = core && /<dcterms:modified[^>]*>([^<]+)</.exec(core);
    return { sheetName: unesc(sh[1]), rows, savedAt: mod ? new Date(mod[1]) : null, formulaCells, uncachedFormulas };
  }

  /* ---------------- price book -> records ----------------------------------------------------- */
  const STORAGE_RE = /(\d+)\s*(GB|TB)\b/i;
  const normStorage = (n, u) => `${parseInt(n, 10)}${u.toUpperCase()}`;

  /** "Galaxy S9 Plus 64GB G965" -> base "Galaxy S9 Plus", storage "64GB", suffix "G965" */
  function splitModel(model) {
    const s = String(model || "").trim(), m = STORAGE_RE.exec(s);
    if (!m) return { base: s, storage: null, suffix: "" };
    return { base: s.slice(0, m.index).trim(), storage: normStorage(m[1], m[2]), suffix: s.slice(m.index + m[0].length).trim() };
  }
  const norm = s => String(s || "").toLowerCase().replace(/^(apple|samsung)\s+/, "").replace(/\bgalaxy\s+/g, "")
    .replace(/\+/g, " plus ").replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
  // Looser key for PROPOSING matches only: ignores 5G/4G, a leading Z, and spacing. Never used to match on its own.
  const relaxed = s => norm(s).split(" ").filter(t => t && t !== "5g" && t !== "4g").join("").replace(/^z(?=(flip|fold))/, "").replace(/(flip|fold)z/, "$1");
  const tokens = s => new Set(norm(s).split(" ").filter(Boolean));

  function parsePriceBook(xl) {
    const head = (xl.rows[0] || []).map(h => String(h ?? "").trim().toLowerCase());
    const col = name => head.findIndex(h => h === name);
    const iRef = col("ref"), iMake = col("make"), iModel = col("model"), iPrice = col("price");
    let iCost = head.findIndex(h => h === "vsg cost"); if (iCost < 0) iCost = head.findIndex(h => h.startsWith("vsg"));
    const missing = [["REF", iRef], ["MODEL", iModel], ["PRICE", iPrice], ["VSG Cost", iCost]].filter(x => x[1] < 0).map(x => x[0]);
    if (missing.length) throw new Error("Couldn't find these columns in the first sheet: " + missing.join(", ") + ".");
    const records = [], warnings = [];
    for (let r = 1; r < xl.rows.length; r++) {
      const row = xl.rows[r] || [];
      if (row[iRef] == null && row[iModel] == null) continue;
      const { base, storage, suffix } = splitModel(row[iModel]);
      const price = typeof row[iPrice] === "number" ? row[iPrice] : null, vsg = typeof row[iCost] === "number" ? row[iCost] : null;
      // A zero base price still produces a "VSG Cost" of the fixed handling fee alone. That is NOT a real cost.
      const usable = price != null && price > 0 && vsg != null && isFinite(vsg) && vsg > 0;
      records.push({ ref: String(row[iRef]), make: row[iMake] || "", model: String(row[iModel] || ""), base, storage, suffix,
                     price, cost: vsg, usable, reason: usable ? "" : price == null || price <= 0 ? "zero base price" : "no cost value" });
    }
    if (xl.uncachedFormulas) warnings.push(`${xl.uncachedFormulas} cost cells have formulas but no saved result. Open the file in Excel, save it, and upload it again.`);
    const seen = new Map();
    for (const r of records) seen.set(r.ref, (seen.get(r.ref) || 0) + 1);
    const dup = [...seen].filter(x => x[1] > 1).map(x => x[0]);
    if (dup.length) warnings.push(`Duplicate REF numbers: ${dup.slice(0, 5).join(", ")}.`);
    return { records, warnings };
  }

  /* ---------------- matching shop variants to price-book records ---------------------------- */
  const shopKey = (model, storage) => `${model}|${storage}`;

  /**
   * shopKeys: [{model, storage}] ; records: from parsePriceBook ; confirmed: { "model|storage": ref }
   * status: matched | confirmed | choose | proposed | no_match | no_cost
   *   matched  = exact name + storage, one record (safe)
   *   confirmed= you chose it
   *   proposed / choose = candidates differ only by 5G/4G or spacing, or several records fit: needs you
   *   no_match = nothing found (suggestions are only suggestions)
   *   no_cost  = matched, but that record has no usable cost (e.g. zero base price)
   */
  function matchAll(shopKeys, records, confirmed) {
    confirmed = confirmed || {};
    const byRef = new Map(records.map(r => [r.ref, r]));
    const exact = new Map(), loose = new Map();
    for (const r of records) {
      if (!r.storage) continue;
      const k = norm(r.base) + "|" + r.storage;
      if (!r.suffix) { (exact.get(k) || exact.set(k, []).get(k)).push(r); }
      const lk = relaxed(r.base) + "|" + r.storage;
      (loose.get(lk) || loose.set(lk, []).get(lk)).push(r);
    }
    const out = [];
    for (const s of shopKeys) {
      const key = shopKey(s.model, s.storage), item = { key, model: s.model, storage: s.storage, status: "no_match", record: null, candidates: [] };
      const c = confirmed[key], cref = c && typeof c === "object" ? c.ref : c;
      const forced = cref != null && byRef.get(String(cref));
      if (forced && forced.storage !== s.storage) {
        // A confirmed match must never carry a different storage size's cost across (e.g. 128GB cost onto a 256GB variant).
        item.rejected = `Confirmed row "${forced.model}" is ${forced.storage || "of unknown storage"}, not ${s.storage}.`;
      } else if (forced) { item.record = forced; item.status = forced.usable ? "confirmed" : "no_cost"; out.push(item); continue; }
      const ex = exact.get(norm(s.model) + "|" + s.storage) || [];
      if (ex.length === 1) { item.record = ex[0]; item.status = ex[0].usable ? "matched" : "no_cost"; out.push(item); continue; }
      if (ex.length > 1) { item.status = "choose"; item.candidates = ex; out.push(item); continue; }
      const lo = (loose.get(relaxed(s.model) + "|" + s.storage) || []);
      if (lo.length) { item.status = lo.length === 1 ? "proposed" : "choose"; item.candidates = lo; out.push(item); continue; }
      // nothing close: offer the most similar names as suggestions only
      const t = tokens(s.model);
      item.candidates = records.filter(r => r.storage === s.storage).map(r => {
        const u = tokens(r.base), inter = [...t].filter(x => u.has(x)).length, jac = inter / (t.size + u.size - inter || 1);
        return { r, jac };
      }).filter(x => x.jac >= 0.5).sort((a, b) => b.jac - a.jac).slice(0, 3).map(x => x.r);
      out.push(item);
    }
    return out;
  }

  /* ---------------- margins ------------------------------------------------------------------ */
  const DEFAULTS = { vatRate: 23, costIncludesVat: false, target: 20, softFloor: 15, hardWarn: 10,
                     mode: "block", gradesConfirmed: false, vatConfirmed: false, maxAgeDays: 45, remember: false };
  const settings = s => Object.assign({}, DEFAULTS, s || {});

  const netRevenue = (price, s) => price / (1 + s.vatRate / 100);
  const netCost = (cost, s) => s.costIncludesVat ? cost / (1 + s.vatRate / 100) : cost;
  /** gross margin as a fraction of ex-VAT revenue (0.2 = 20%) */
  function margin(price, cost, s) { s = settings(s); const n = netRevenue(price, s); return (n - netCost(cost, s)) / n; }
  function profit(price, cost, s) { s = settings(s); return netRevenue(price, s) - netCost(cost, s); }
  /** lowest price (VAT included) that still gives this margin (percent) */
  function priceForMargin(pct, cost, s) { s = settings(s); return netCost(cost, s) / (1 - pct / 100) * (1 + s.vatRate / 100); }

  const eur0 = n => "€" + Math.round(n).toLocaleString("en-IE");
  const pc = m => (m * 100).toFixed(1).replace(/\.0$/, "") + "%";

  /**
   * Adds margin facts to a row and applies margin protection to its recommendation.
   * row: { vs_price, recommended } (recommended is the ORIGINAL recommendation; a modified copy is returned in row.rec)
   * cost: number | null
   * Rules: a price that sells at a loss is always sent to manual review. In "block" mode a price below the soft
   * floor (for cuts) or below the hard warning (for anything) is sent to manual review too. In "warn" mode they
   * only add a warning. Raises are never blocked for margin.
   */
  function assess(row, cost, s) {
    s = settings(s);
    const out = { cost: cost == null ? null : cost, marginNow: null, marginNew: null, profitNow: null, profitNew: null,
                  minPrice: null, targetPrice: null, level: null, flags: [], rec: row.recommended ? JSON.parse(JSON.stringify(row.recommended)) : null };
    if (cost == null) { out.flags.push("cost_missing"); return out; }
    out.minPrice = priceForMargin(s.softFloor, cost, s); out.targetPrice = priceForMargin(s.target, cost, s);
    if (row.vs_price != null) {
      out.marginNow = margin(row.vs_price, cost, s); out.profitNow = profit(row.vs_price, cost, s);
      if (out.marginNow < 0) out.flags.push("loss_now"); else if (out.marginNow < s.softFloor / 100) out.flags.push("below_margin_floor_now");
    }
    const rec = out.rec;
    if (!rec) return out;
    out.marginNew = margin(rec.price, cost, s); out.profitNew = profit(rec.price, cost, s);
    const m = out.marginNew, down = rec.direction === "down", already = rec.kind === "manual_review";
    const worse = out.marginNow == null || m < out.marginNow - 1e-9;   // a raise that improves a thin margin is never blocked for margin
    const note = ` Margin after this change: ${pc(m)} (target ${s.target}%).`;
    if (m < 0) {
      out.level = "loss"; out.flags.push("loss_making_price");
      if (!already && worse) { rec.why_blocked = `This price would sell at a loss of about ${eur0(-profit(rec.price, cost, s))} per phone before other costs.`; rec.kind = "manual_review"; }
      else rec.reason += already ? " It would also sell at a loss." : ` Still a loss after this change (${pc(m)} margin).`;
    } else if (m < s.hardWarn / 100) {
      out.level = "hard"; out.flags.push("below_hard_margin");
      if (s.mode === "block" && !already && worse) { rec.why_blocked = `Margin would be only ${pc(m)}, under your ${s.hardWarn}% warning level.`; rec.kind = "manual_review"; }
      else rec.reason += note;
    } else if (m < s.softFloor / 100 && (down || m < (out.marginNow ?? 1))) {
      out.level = "soft"; out.flags.push("below_margin_floor");
      if (s.mode === "block" && down && !already) { rec.why_blocked = `Margin would be ${pc(m)}, under your ${s.softFloor}% floor. The lowest price that keeps ${s.softFloor}% is ${eur0(out.minPrice)}.`; rec.kind = "manual_review"; }
      else rec.reason += note;
    } else if (m >= s.target / 100) out.level = "ok";
    else out.level = "ok";
    if (rec.kind === "manual_review" && rec.why_blocked && !rec.reason.includes(rec.why_blocked)) rec.reason = rec.why_blocked;
    return out;
  }

  /**
   * Margin check for ANY price, including one typed in by hand. level: ok | soft | hard | loss.
   * soft = under the floor, hard = under the hard-warning margin, loss = below cost (ex-VAT revenue < cost).
   */
  function checkPrice(price, cost, s) {
    s = settings(s);
    if (cost == null || price == null) return { level: null, margin: null, profit: null, minPrice: null, message: "" };
    const m = margin(price, cost, s), pr = profit(price, cost, s), min = priceForMargin(s.softFloor, cost, s);
    const level = m < 0 ? "loss" : m < s.hardWarn / 100 ? "hard" : m < s.softFloor / 100 ? "soft" : "ok";
    const message = level === "loss" ? `Below cost: a loss of about ${eur0(-pr)} per phone before other costs.`
      : level === "hard" ? `Margin ${pc(m)} is under your ${s.hardWarn}% warning level.`
      : level === "soft" ? `Margin ${pc(m)} is under your ${s.softFloor}% floor (lowest price for the floor: ${eur0(min)}).` : "";
    return { level, margin: m, profit: pr, minPrice: min, message };
  }

  const costSig = (cost, s) => { s = settings(s); return `${Math.round(cost * 100)}|${s.vatRate}|${s.costIncludesVat ? 1 : 0}`; };

  /**
   * Is a saved approval still good to act on?
   *  evidence: Refurbed's cheapest price moved by more than EUR5 (or appeared/disappeared) since you approved, the approval
   *            has no snapshot of the evidence (older versions), or it is more than maxHours old.
   *  loss    : the price is below cost now and the "sell below cost anyway" tick is missing, or was given for a different
   *            cost or VAT setting than today's.
   * d: { refPrice, at, lossOk, lossSig } ; cur: { refPrice, cost, price }
   */
  function approvalIssues(d, cur, s, now, maxHours) {
    const out = { evidence: "", loss: "" };
    maxHours = maxHours == null ? 48 : maxHours;
    const eurs = n => "\u20ac" + (Math.round(n * 100) / 100).toFixed(2);
    if (d.refPrice === undefined) out.evidence = "This approval has no record of the Refurbed price it was based on.";
    else if ((d.refPrice == null) !== (cur.refPrice == null) || (d.refPrice != null && Math.abs(d.refPrice - cur.refPrice) > 5))
      out.evidence = `Refurbed's cheapest price moved from ${d.refPrice == null ? "none" : eurs(d.refPrice)} to ${cur.refPrice == null ? "none" : eurs(cur.refPrice)} since you approved this.`;
    else if (d.at && ((now || new Date()) - new Date(d.at)) > maxHours * 36e5) out.evidence = `Approved more than ${maxHours} hours ago.`;
    if (cur.cost != null && checkPrice(d.price, cur.cost, s).level === "loss") {
      if (!d.lossOk) out.loss = "Sells below cost. Edit the price, or choose \u201cSell below cost anyway\u201d when editing it.";
      else if (d.lossSig !== costSig(cur.cost, s)) out.loss = "The cost or VAT setting changed since you accepted selling below cost. Confirm it again by editing the price.";
    }
    return out;
  }

  /** how old is the sheet? */
  function ageDays(savedAt, now) { return savedAt ? Math.floor(((now || new Date()) - new Date(savedAt)) / 864e5) : null; }

  return { readXlsx, parsePriceBook, splitModel, norm, relaxed, matchAll, shopKey, margin, profit, priceForMargin, assess, checkPrice, costSig, approvalIssues, ageDays, settings, DEFAULTS };
});
