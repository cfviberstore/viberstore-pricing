const test = require("node:test"), assert = require("node:assert");
const C = require("../js/costs.js");

const book = rows => ({ rows: [["REF","MAKE","MODEL","PRICE","ROE",1.99,"VSG Cost"]].concat(rows), uncachedFormulas: 0 });
const rec = (ref, model, price, cost) => [ref, "Apple", model, price, null, null, cost];
const pb = rows => C.parsePriceBook(book(rows));

test("margin maths: 20% margin on a EUR300 cost needs EUR461.25 incl. 23% VAT", () => {
  assert.equal(Math.round(C.priceForMargin(20, 300) * 100) / 100, 461.25);
  assert.ok(Math.abs(C.margin(461.25, 300) - 0.2) < 1e-9);
  assert.ok(C.margin(300, 300) < 0);                               // selling at cost incl. VAT is still a loss? no: ex-VAT revenue 243.9 < 300
  assert.ok(Math.abs(C.margin(461.25, 369, { costIncludesVat: true }) - 0.2) < 1e-9);
});

test("a zero base price is never a usable cost, even though the sheet shows EUR5", () => {
  const r = pb([rec(1, "iPhone 4 16GB", 0, 5), rec(2, "iPhone 15 Pro 128GB", 401.84, 342.22), rec(3, "iPhone X 64GB", null, 5)]).records;
  assert.deepEqual(r.map(x => x.usable), [false, true, false]);
  assert.match(r[0].reason, /zero/);
});

test("missing columns and uncached formulas are reported", () => {
  assert.throws(() => C.parsePriceBook({ rows: [["A", "B"]] }), /Couldn't find these columns/);
  const x = book([rec(1, "iPhone 11 64GB", 100, 117)]); x.uncachedFormulas = 3;
  assert.match(C.parsePriceBook(x).warnings[0], /no saved result/);
});

const recs = pb([
  rec(1, "iPhone 13 Pro Max 256GB", 400, 340),
  rec(2, "Galaxy Z Flip 6 256GB", 300, 260),
  rec(3, "Galaxy A52 5G 128GB", 100, 90),
  rec(4, "Galaxy A25 5G 128GB", 103, 91.7), rec(5, "Galaxy A25 5G 128GB", 110, 97.4),
  rec(6, "Galaxy S9 Plus 64GB G965", 40, 44),
  rec(7, "iPhone 4 16GB", 0, 5),
  rec(8, "Galaxy A52 4G 128GB", 0, 5),
]).records;
const shop = (m, s) => ({ model: m, storage: s });
const one = (m, s, conf) => C.matchAll([shop(m, s)], recs, conf)[0];

test("exact name + storage matches; nothing else is matched without confirmation", () => {
  const a = one("iPhone 13 Pro Max", "256GB");
  assert.equal(a.status, "matched"); assert.equal(a.record.ref, "1");
  assert.equal(one("Samsung Galaxy Flip 6", "256GB").status, "proposed");       // "Z Flip 6": close, needs you
  assert.equal(one("Samsung Galaxy A52", "128GB").status, "choose");            // 4G and 5G rows both fit: needs you
  assert.equal(one("iPhone 13 Pro Max", "512GB").status, "no_match");           // wrong storage never matches
});

test("duplicate rows are ambiguous, suffixed rows are not exact matches, zero-cost rows are flagged", () => {
  assert.equal(one("Samsung Galaxy A25 5G", "128GB").status, "choose");
  assert.notEqual(one("Samsung Galaxy S9 Plus", "64GB").status, "matched");
  assert.equal(one("iPhone 4", "16GB").status, "no_cost");
});

test("a confirmed choice is used; a confirmed row without a cost is still no cost", () => {
  const c = one("Samsung Galaxy A25 5G", "128GB", { "Samsung Galaxy A25 5G|128GB": "5" });
  assert.equal(c.status, "confirmed"); assert.equal(c.record.cost, 97.4);
  assert.equal(one("Samsung Galaxy A52", "128GB", { "Samsung Galaxy A52|128GB": "8" }).status, "no_cost");
});

test("suggestions for unmatched names are only suggestions", () => {
  const m = one("Samsung Galaxy Flip 9", "256GB");
  assert.notEqual(m.status, "matched"); assert.equal(m.record, null);
});

const R = (price, newPrice, kind = "market") => ({ vs_price: price, recommended: newPrice == null ? null : { price: newPrice, kind, direction: newPrice > price ? "up" : "down", reason: "Refurbed is cheaper." } });

test("a price that sells at a loss always goes to manual review, even in warn mode", () => {
  const a = C.assess(R(400, 320), 300, { mode: "warn" });      // 320/1.23=260 < 300
  assert.equal(a.rec.kind, "manual_review"); assert.ok(a.flags.includes("loss_making_price")); assert.match(a.rec.reason, /loss/);
});

test("low margin: warn mode only warns, block mode sends cuts to review", () => {
  const price = 420, cost = 300;                                // 420 -> 341.5 net, margin 12.1%
  const w = C.assess(R(480, price), cost, { mode: "warn" });
  assert.equal(w.rec.kind, "market"); assert.match(w.rec.reason, /Margin after this change/);
  const b = C.assess(R(480, price), cost, { mode: "block" });
  assert.equal(b.rec.kind, "manual_review"); assert.match(b.rec.reason, /floor/);
});

test("hard warning level and raises", () => {
  const h = C.assess(R(480, 390), 300, { mode: "block" });      // 390/1.23=317 -> 5.4%
  assert.equal(h.level, "hard"); assert.equal(h.rec.kind, "manual_review");
  const up = C.assess(R(300, 330), 280, { mode: "block" });     // loss now, raise improves it: not blocked for margin
  assert.equal(up.rec.kind, "market");
});

test("no cost: flagged, recommendation untouched", () => {
  const a = C.assess(R(400, 380), null, {});
  assert.ok(a.flags.includes("cost_missing")); assert.equal(a.rec.kind, "market");
});

test("rows with no recommendation still report margin today", () => {
  const a = C.assess(R(300, null), 280, {});
  assert.ok(a.flags.includes("loss_now")); assert.equal(a.rec, null);
});

test("age in days", () => {
  assert.equal(C.ageDays("2026-09-01T00:00:00Z", new Date("2026-11-19T00:00:00Z")), 79);
});

const FIX = process.env.PRICEBOOK_FIXTURE;
test("reads a real .xlsx file", { skip: !FIX }, async () => {
  const xl = await C.readXlsx(require("fs").readFileSync(FIX));
  const out = C.parsePriceBook(xl);
  assert.equal(xl.sheetName, "Sept");
  assert.equal(out.records.length, 3);
  assert.equal(out.records[0].model, "iPhone 15 Pro 128GB");
  assert.ok(Math.abs(out.records[0].cost - 342.22) < 0.01);
  assert.equal(out.records[2].usable, false);
  assert.ok(xl.savedAt instanceof Date);
});

test("a confirmed match with a different storage size is rejected, never used", () => {
  const rs = pb([rec(10, "iPhone 14 128GB", 300, 250), rec(11, "iPhone 14 256GB", 330, 280)]).records;
  const m = C.matchAll([shop("iPhone 14", "256GB")], rs, { "iPhone 14|256GB": "10" })[0];      // 128GB cost onto a 256GB variant
  assert.match(m.rejected, /128GB/);
  assert.equal(m.status, "matched"); assert.equal(m.record.ref, "11");                          // falls back to the exact 256GB row, never the wrong one
  const none = C.matchAll([shop("iPhone 14", "512GB")], rs, { "iPhone 14|512GB": "10" })[0];
  assert.equal(none.record, null); assert.match(none.rejected, /128GB/);
});

test("confirmed matches accept the audited object form and the old plain form", () => {
  const ref = "5";
  const a = one("Samsung Galaxy A25 5G", "128GB", { "Samsung Galaxy A25 5G|128GB": { ref, recordModel: "Galaxy A25 5G 128GB", at: "2026-11-19" } });
  const b = one("Samsung Galaxy A25 5G", "128GB", { "Samsung Galaxy A25 5G|128GB": ref });
  assert.equal(a.status, "confirmed"); assert.equal(b.status, "confirmed"); assert.equal(a.record.cost, b.record.cost);
});

test("checkPrice grades any price, including one typed by hand", () => {
  assert.equal(C.checkPrice(461.25, 300).level, "ok");
  assert.equal(C.checkPrice(430, 300).level, "soft");           // 430/1.23=349.6 -> 14.2%
  assert.equal(C.checkPrice(390, 300).level, "hard");           // 5.4%
  const l = C.checkPrice(299.99, 300); assert.equal(l.level, "loss"); assert.match(l.message, /Below cost/);
  assert.equal(C.checkPrice(400, null).level, null);
});

test("an old cost sheet never changes the rules: block mode still holds a low-margin cut", () => {
  // staleness is a warning shown in the UI; the assess() rules take no age input at all
  const b = C.assess(R(480, 420), 300, { mode: "block", maxAgeDays: 1 });
  assert.equal(b.rec.kind, "manual_review");
});

test("defaults: protection on, estimates until VAT and grades are confirmed, session-only storage", () => {
  const d = C.DEFAULTS;
  assert.equal(d.mode, "block"); assert.equal(d.vatConfirmed, false); assert.equal(d.gradesConfirmed, false); assert.equal(d.remember, false);
});

const NOW = new Date("2026-11-19T12:00:00Z"), HOUR = 36e5;
const ap = (over) => Object.assign({ price: 400, refPrice: 380, at: new Date(NOW - HOUR).toISOString() }, over);

test("an approval stays good while the Refurbed evidence holds", () => {
  const i = C.approvalIssues(ap(), { refPrice: 382, cost: 250 }, {}, NOW);
  assert.equal(i.evidence, ""); assert.equal(i.loss, "");
});

test("an approval is sent back for re-review when Refurbed's price moves, disappears, or the approval is old", () => {
  assert.match(C.approvalIssues(ap(), { refPrice: 340, cost: 250 }, {}, NOW).evidence, /moved from .380.00 to .340.00/);
  assert.match(C.approvalIssues(ap(), { refPrice: null, cost: 250 }, {}, NOW).evidence, /to none/);
  assert.match(C.approvalIssues(ap({ at: new Date(NOW - 60 * HOUR).toISOString() }), { refPrice: 380, cost: 250 }, {}, NOW).evidence, /48 hours/);
  assert.match(C.approvalIssues(ap({ refPrice: undefined }), { refPrice: 380, cost: 250 }, {}, NOW).evidence, /no record/);
});

test("a below-cost override only holds for the cost and VAT settings it was given under", () => {
  const sig = C.costSig(340, {});
  const d = ap({ price: 299.99, lossOk: true, lossSig: sig });
  assert.equal(C.approvalIssues(d, { refPrice: 380, cost: 340 }, {}, NOW).loss, "");
  assert.match(C.approvalIssues(d, { refPrice: 380, cost: 345 }, {}, NOW).loss, /changed/);
  assert.match(C.approvalIssues(d, { refPrice: 380, cost: 340 }, { vatRate: 13.5 }, NOW).loss, /changed|Sells below/);
  assert.match(C.approvalIssues(ap({ price: 299.99 }), { refPrice: 380, cost: 340 }, {}, NOW).loss, /Sells below cost/);
  assert.equal(C.approvalIssues(ap({ price: 299.99 }), { refPrice: 380, cost: 200 }, {}, NOW).loss, "");
});
