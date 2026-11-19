const test = require("node:test"), assert = require("node:assert");
const X = require("../js/shopify_export.js");

const H = ["Handle","Title","Option1 Name","Option1 Value","Option2 Name","Option2 Value","Variant SKU","Variant Price","Image Src"];
const fresh = rows => X.toCsv([H].concat(rows));
const base = [
  ["iphone-13","iPhone 13, 128GB","Memory","128GB","Colour","Blue","SKU-A","399.99","http://img"],
  ["iphone-13","","Memory","128GB","Colour","Red","SKU-B","399.99",""],
  ["iphone-14","iPhone 14","Memory","128GB","Colour","Blue","SKU-C","499.99",""],
];
const item = (over) => Object.assign({ key: "k1", handle: "iphone-13", price: 379.99, reason: "market",
  variants: [{ id: 1, sku: "SKU-A", price: 399.99, options: ["128GB","Blue"] }, { id: 2, sku: "SKU-B", price: 399.99, options: ["128GB","Red"] }] }, over);

test("applies price to every colour, keeps all other columns and rows", () => {
  const r = X.buildExport(fresh(base), [item()]);
  assert.ok(r.ok); assert.equal(r.applied.length, 2);
  const o = X.parseCsv(r.csv);
  assert.equal(o.length, 3);                       // header + the two iphone-13 rows, not iphone-14
  assert.equal(o[1][1], "iPhone 13, 128GB");        // Title preserved (with comma)
  assert.equal(o[1][7], "379.99"); assert.equal(o[2][7], "379.99");
  assert.equal(o[1][8], "http://img");
});
test("blocks when Shopify price changed since the scan", () => {
  const b = base.map(x => x.slice()); b[0][7] = "389.99";
  const r = X.buildExport(fresh(b), [item()]);
  assert.equal(r.ok, false); assert.match(r.blocked[0].reason, /costs 389.99 in Shopify now/);
});
test("blocks a missing SKU, a duplicate SKU and a SKU on the wrong product", () => {
  assert.match(X.buildExport(fresh(base), [item({ variants: [{ id: 9, sku: "", price: 1, options: [] }] })]).blocked[0].reason, /no SKU/);
  assert.match(X.buildExport(fresh(base), [item({ variants: [{ id: 9, sku: "NOPE", price: 1, options: [] }] })]).blocked[0].reason, /not in the fresh export/);
  const dup = base.concat([["iphone-13","","Memory","128GB","Colour","Green","SKU-A","399.99",""]]);
  assert.match(X.buildExport(fresh(dup), [item()]).blocked[0].reason, /appears 2 times/);
  assert.match(X.buildExport(fresh(base), [item({ handle: "iphone-14" })]).blocked[0].reason, /now belongs to/);
});
test("one bad colour holds the whole product back, other products still export", () => {
  const items = [item({ variants: [{ id: 1, sku: "SKU-A", price: 399.99, options: ["128GB","Blue"] }, { id: 2, sku: "SKU-B", price: 1, options: ["128GB","Red"] }] }),
                 item({ key: "k2", handle: "iphone-14", price: 459.99, variants: [{ id: 3, sku: "SKU-C", price: 499.99, options: ["128GB","Blue"] }] })];
  const r = X.buildExport(fresh(base), items);
  assert.ok(r.ok); assert.equal(r.applied.length, 1); assert.equal(r.blocked.length, 1);
  assert.ok(!r.csv.includes("iphone-13"));
});
test("rejects wrong file, empty approvals and silly prices", () => {
  assert.match(X.buildExport("a,b\n1,2", [item()]).fatal, /Missing columns/);
  assert.match(X.buildExport(fresh(base), []).fatal, /Nothing is approved/);
  assert.equal(X.buildExport(fresh(base), [item({ price: 0 })]).ok, false);
});
test("warns on large changes and handles quoted newlines / BOM", () => {
  const r = X.buildExport("﻿" + fresh(base), [item({ price: 199.99 })]);
  assert.ok(r.ok); assert.ok(r.warnings.length === 2);
  assert.deepEqual(X.parseCsv('a,b\r\n"x\ny","q""z"\r\n')[1], ["x\ny", 'q"z']);
});

test("an item carrying a blockReason is left out and reported, others still export", () => {
  const r = X.buildExport(fresh(base), [item({ blockReason: "Sells below cost" }), item({ key: "k2", handle: "iphone-14", price: 459.99, variants: [{ id: 3, sku: "SKU-C", price: 499.99, options: ["128GB","Blue"] }] })]);
  assert.ok(r.ok); assert.equal(r.blocked[0].reason, "Sells below cost"); assert.equal(r.applied.length, 1);
  assert.ok(!r.csv.includes("iphone-13"));
});
