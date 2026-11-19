import json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scraper"))

import engine
from engine import Policy, build_model, snap_x999, snap_x999_up, market_target
from refurbed_scraper import (canonical_name, normalise_storage, parse_delta, parse_offer,
                              parse_selects, slug_candidates, Refurbed, parse_gtm, parse_variant_page,
                              choose_reference, usable_offer, DEFAULT_RC)
from shopify_scraper import extract_models, normalise_condition

FIX = open(os.path.join(HERE, "fixtures", "refurbed_iphone13pro_min.html"), encoding="utf8").read()
P = Policy()


def v(price, stock=True, spread=0):
    return {"price": price, "in_stock": stock, "price_spread": spread}


def r(price, stock=True):
    return {"price": price, "in_stock": stock, "url": "https://x"}


class RefurbedParsing(unittest.TestCase):
    def test_delta(self):
        self.assertEqual(parse_delta("more,+€55.00"), 55.0)
        self.assertEqual(parse_delta("less,-€3.00"), -3.0)
        self.assertEqual(parse_delta("more,+€1,204.50"), 1204.5)
        self.assertEqual(parse_delta(""), 0.0)

    def test_storage(self):
        self.assertEqual(normalise_storage("128 GB"), "128GB")
        self.assertEqual(normalise_storage("1000 GB"), "1TB")
        self.assertEqual(normalise_storage("2 TB"), "2TB")
        self.assertIsNone(normalise_storage("blue"))

    def test_selects(self):
        s = parse_selects(FIX)
        self.assertEqual(s["grades"], {"Good": "c", "V. Good": "b", "Excellent": ""})  # Premium ignored
        self.assertEqual(s["storages"], {"128GB": "14171", "256GB": "14172", "512GB": "14173", "1TB": "14174"})
        self.assertEqual(s["selected"], ("V. Good", "128GB"))
        self.assertEqual(s["colour_min_delta"], 0.0)

    def test_offer(self):
        o = parse_offer(FIX)
        self.assertEqual(o["price"], 367.99)
        self.assertTrue(o["in_stock"])
        self.assertTrue(o["url"].endswith("/p/iphone-13-pro/14171b/"))

    def test_colour_adjustment(self):
        html = FIX.replace('data-price=""  > blue', 'data-price="" > blue').replace(
            "</select>\n<script", '</select><select id="product-color"><option value="/p/x/1c/" data-price="less,-€9.99"> grey </option>'
            '<option value="/p/x/2c/" selected="selected" data-price=""> blue </option></select>\n<script')
        self.assertEqual(parse_selects(html)["colour_min_delta"], -9.99)

    def test_name_matching_is_strict(self):
        self.assertEqual(canonical_name("Samsung Galaxy S25+"), canonical_name("Galaxy S25 Plus"))
        self.assertNotEqual(canonical_name("iPhone 15"), canonical_name("iPhone 15 Pro"))
        self.assertNotEqual(canonical_name("Samsung Galaxy S24"), canonical_name("Samsung Galaxy S24 FE"))

    def test_slug_candidates(self):
        self.assertIn("samsung-galaxy-s25-plus", slug_candidates("Samsung Galaxy S25+"))
        self.assertIn("samsung-galaxy-a55-5g", slug_candidates("Samsung Galaxy A55 5G"))
        self.assertIn("samsung-galaxy-a55", slug_candidates("Samsung Galaxy A55 5G"))
        self.assertIn("iphone-17e", slug_candidates("Apple iPhone 17e"))
        self.assertIn("samsung-galaxy-s22-ultra-5g", slug_candidates("Samsung Galaxy S22 Ultra"))


class ShopifyParsing(unittest.TestCase):
    def prod(self, title, opts, variants, vendor="Apple"):
        return {"title": title, "vendor": vendor, "handle": "h", "product_type": "Mobile Phones",
                "options": [{"name": n} for n in opts], "variants": variants}

    def test_stock_aware_price_and_tolerant_options(self):
        p = self.prod("Apple iPhone 15", ["Memory", "Condition ", "Colour"], [
            {"id": 1, "option1": "128GB", "option2": "Good", "option3": "Blue", "price": "400.00", "available": False},
            {"id": 2, "option1": "128GB", "option2": "Good", "option3": "Pink", "price": "429.99", "available": True},
            {"id": 3, "option1": "128GB", "option2": "Very Good - New Battery", "option3": "Pink", "price": "1", "available": True},
        ])
        models, skipped = extract_models([p], ["Apple"])
        c = models["iPhone 15"]["combos"][("Good", "128GB")]
        self.assertEqual(c["price"], 429.99)       # sold-out cheaper colour must NOT win
        self.assertTrue(c["in_stock"])
        self.assertEqual(skipped["new_battery_variants"], 1)

    def test_all_sold_out_falls_back(self):
        p = self.prod("iPhone 14", ["Memory", "Condition", "Colour"], [
            {"id": 1, "option1": "128GB", "option2": "Excellent", "option3": "Blue", "price": "500", "available": False}])
        c = extract_models([p], ["Apple"])[0]["iPhone 14"]["combos"][("Excellent", "128GB")]
        self.assertEqual((c["price"], c["in_stock"]), (500.0, False))

    def test_vendor_filter_and_condition_names(self):
        self.assertEqual(normalise_condition("Very Good"), "V. Good")
        p = self.prod("Moto G", ["Memory", "Condition", "Colour"], [], vendor="Motorola")
        self.assertEqual(extract_models([p], ["Apple", "Samsung"])[0], {})


class Pricing(unittest.TestCase):
    def test_snap(self):
        self.assertEqual(snap_x999(667.5), 669.99)
        self.assertEqual(snap_x999(450.99), 449.99)
        self.assertEqual(snap_x999_up(430.0), 439.99)
        self.assertEqual(snap_x999_up(429.99), 429.99)

    def test_market_target_never_above_competitor(self):
        for ref in (364.99, 369.99, 371.0, 1299.0, 99.5):
            self.assertLessEqual(market_target(ref, P), ref)

    def test_default_matches_refurbed_on_the_x999_ladder(self):
        self.assertEqual(P.undercut_eur, 0)
        for ref, want in ((499.99, 499.99), (505.0, 499.99), (509.99, 509.99), (512.0, 509.99), (498.0, 489.99), (500.0, 499.99), (1299.0, 1299.0 - 9.01)):
            self.assertEqual(market_target(ref, P), want, ref)

    def test_undercut_is_configurable_and_stays_on_x999(self):
        self.assertEqual(market_target(499.99, Policy(undercut_eur=5)), 489.99)
        self.assertEqual(market_target(499.99, Policy(undercut_eur=10)), 489.99)
        self.assertEqual(market_target(509.99, Policy(undercut_eur=10)), 499.99)

    def test_every_target_ends_in_x9_99_and_is_never_above_the_competitor(self):
        import random
        rnd = random.Random(3)
        for _ in range(2000):
            ref = round(rnd.uniform(50, 1800), 2)
            for u in (0, 5, 10):
                t = market_target(ref, Policy(undercut_eur=u))
                self.assertEqual(round(t * 100) % 1000, 999, (ref, u, t))
                self.assertLessEqual(t, ref + 1e-9)
                self.assertGreater(t, ref - u - 10.01)       # never gives away more than one extra step

    def test_pricing_to_a_499_99_competitor_suggests_499_99_not_489_99(self):
        rows = build_model({("Good", "128GB"): v(559.99)}, {("Good", "128GB"): rref(499.99)}, P)
        self.assertEqual(rows[0]["recommended"]["price"], 499.99)
        rows = build_model({("Good", "128GB"): v(449.99)}, {("Good", "128GB"): rref(499.99)}, P)
        self.assertEqual((rows[0]["recommended"]["price"], rows[0]["recommended"]["kind"]), (489.99, "capped"))   # +11% raise limited to +10%
        rows = build_model({("Good", "128GB"): v(459.99)}, {("Good", "128GB"): rref(499.99)}, P)
        self.assertEqual(rows[0]["recommended"]["price"], 499.99)

    def test_a_lone_cheap_offer_is_not_matched_when_cutting(self):
        # cheapest 499.99 stands alone, next offer 539.99: the cut aims at the corroborated price, not the lone one
        ref = rref(499.99, supported=539.99, typical=539.99, flags=["lone_cheap_offer"])
        rows = build_model({("Good", "128GB"): v(600.0)}, {("Good", "128GB"): ref}, P)
        self.assertEqual(rows[0]["recommended"]["price"], 539.99)

    def row(self, rows, cond, st):
        return next(x for x in rows if x["condition"] == cond and x["storage"] == st)

    def test_competitive_band_holds(self):
        rows = build_model({("Good", "128GB"): v(359.99)}, {("Good", "128GB"): r(364.99)}, P)
        x = rows[0]
        self.assertEqual(x["status"], "competitive")
        self.assertIsNone(x["recommended"])

    def test_lower_when_dearer(self):
        x = build_model({("Good", "128GB"): v(449.99)}, {("Good", "128GB"): r(364.99)}, P)[0]
        self.assertEqual(x["status"], "refurbed_cheaper")
        self.assertEqual(x["recommended"]["direction"], "down")
        self.assertLess(x["recommended"]["price"], 364.99)
        self.assertEqual(x["delta"], 85.0)

    def test_raise_is_capped_per_step(self):
        x = build_model({("Good", "128GB"): v(219.99)}, {("Good", "128GB"): r(426.0)}, P)[0]
        self.assertEqual(x["status"], "vs_cheaper")
        self.assertEqual(x["recommended"]["direction"], "up")
        self.assertLessEqual(x["recommended"]["price"], snap_x999(219.99 * 1.10))
        self.assertIn("review_large_gap", x["flags"])

    def test_ref_out_of_stock_gives_no_market_advice(self):
        x = build_model({("Good", "128GB"): v(449.99)}, {("Good", "128GB"): r(364.99, stock=False)}, P)[0]
        self.assertEqual(x["status"], "ref_out_of_stock")
        self.assertIsNone(x["recommended"])

    def test_condition_ladder_is_enforced_even_without_ref_data(self):
        vs = {("Good", "64GB"): v(209.99), ("V. Good", "64GB"): v(209.99), ("Excellent", "64GB"): v(259.99)}
        rows = build_model(vs, {}, P)
        x = self.row(rows, "V. Good", "64GB")
        self.assertEqual(x["recommended"]["price"], 219.99)
        self.assertIn("ladder_condition", x["recommended"]["reasons"])
        self.assertIn("ladder_violation", x["flags"])

    def test_storage_ladder_uses_post_recommendation_prices(self):
        vs = {("Good", "128GB"): v(449.99), ("Good", "256GB"): v(459.99)}
        ref = {("Good", "128GB"): r(364.99), ("Good", "256GB"): r(382.99)}
        rows = build_model(vs, ref, P)
        a, b = self.row(rows, "Good", "128GB"), self.row(rows, "Good", "256GB")
        self.assertGreaterEqual(b["recommended"]["price"] - a["recommended"]["price"], P.min_storage_gap_eur - 0.01)

    def test_ref_anomaly_flagged(self):
        vs = {("Good", "256GB"): v(300), ("Good", "512GB"): v(340)}
        ref = {("Good", "256GB"): r(436.99), ("Good", "512GB"): r(429.99)}
        rows = build_model(vs, ref, P)
        self.assertTrue(all("ref_anomaly" in x["flags"] for x in rows))

    def test_out_of_stock_variants_deprioritised(self):
        a = build_model({("Good", "128GB"): v(449.99, stock=True)}, {("Good", "128GB"): r(364.99)}, P)[0]
        b = build_model({("Good", "128GB"): v(449.99, stock=False)}, {("Good", "128GB"): r(364.99)}, P)[0]
        self.assertGreater(a["priority"], b["priority"])

    def test_summary(self):
        rows = build_model({("Good", "128GB"): v(449.99), ("Good", "256GB"): v(300)},
                           {("Good", "128GB"): r(364.99), ("Good", "256GB"): r(400)}, P)
        s = engine.summarise([{"brand": "apple", "model": "m", "variants": rows}])
        self.assertEqual((s["refurbed_cheaper"], s["vs_cheaper"], s["win_rate"]), (1, 1, 50.0))


# ----------------------------------------------------------------------------- comparable offers
def page(stem, price, colour="black", battery="optimal", sim="Dual-SIM (eSIM, Nano-SIM)", seller="Shop A",
         colours=(), batteries=(), stock=True, grade="V. Good", storage="128 GB", slug="m"):
    """Builds a minimal Refurbed variant page (selects + BuyAction JSON-LD + gtmData)."""
    def opts(items, sel_label):
        out = ""
        for label, delta, url in items:
            sel = ' selected="selected"' if label == sel_label else ""
            dp = "" if delta == 0 else f"more,+€{delta}" if delta > 0 else f"less,-€{abs(delta)}"
            out += f'<option value="{url}"{sel} data-price="{dp}">{label}</option>'
        return out
    col = [(colour, 0, f"/p/{slug}/{stem}/")] + [(n, d, u) for n, d, u in colours]
    bat = list(batteries)
    ld = {"object": {"name": "M", "offers": {"price": str(price), "availability": "https://schema.org/" + ("InStock" if stock else "OutOfStock"),
                                              "url": f"https://www.refurbed.ie/p/{slug}/{stem}/"}}}
    gtm = {"item": {"item_variant": f"{storage} | {sim} | {colour} | Grade B ({stem})", "battery_condition": battery, "color": colour}}
    import json as _j
    return (f'<select id="product-grade"><option value="/p/{slug}/{stem}/" selected="selected">Very good</option></select>'
            f'<select id="product-storage"><option value="/p/{slug}/{stem}/" selected="selected">{storage}</option></select>'
            f'<select id="product-color">{opts(col, colour)}</select>'
            + (f'<select id="product-battery">{bat[0]}</select>' if bat else "")
            + f'<a data-test="merchant-name" href="/m/x/"> {seller} </a><span data-test="ships-from"> Germany </span>'
            f'<script data-test="product-buy-action-schema">{_j.dumps(ld)}</script>'
            f'<script>var gtmData = {_j.dumps(gtm)}; var other = 1;</script>')


class FakeClient:
    def __init__(self, pages): self.pages, self.errors, self.requested = pages, 0, []
    def get(self, path):
        self.requested.append(path)
        return (200, self.pages[path], path) if path in self.pages else (404, "", path)


def scraper_with(pages):
    r = Refurbed.__new__(Refurbed)
    r.client, r.rc, r.workers = FakeClient(pages), dict(DEFAULT_RC), 1
    return r


EXPECT = ("V. Good", "128GB")


class ComparableOffers(unittest.TestCase):
    def test_gtm_and_variant_page(self):
        h = page("1b", 304, battery="optimal", seller="Online-Phones 24")
        self.assertEqual(parse_gtm(h)["battery_condition"], "optimal")
        pg = parse_variant_page(h)
        self.assertEqual((pg["price"], pg["battery"], pg["sim"], pg["seller"]), (304.0, "optimal", "Dual-SIM (eSIM, Nano-SIM)", "Online-Phones 24"))

    def test_usable_offer_rules(self):
        rc = DEFAULT_RC
        base = {"in_stock": True, "battery": "optimal", "sim": "Dual-SIM (eSIM, Nano-SIM)"}
        self.assertEqual(usable_offer(base, rc), (True, None))
        self.assertEqual(usable_offer({**base, "battery": "new"}, rc), (False, "new_battery"))
        self.assertEqual(usable_offer({**base, "sim": "Dual-SIM (2 eSIMs)"}, rc), (False, "esim_only"))
        self.assertEqual(usable_offer({**base, "in_stock": False}, rc), (False, "out_of_stock"))
        self.assertEqual(usable_offer({**base, "battery": None, "sim": None}, rc), (True, None))  # older phones: no label

    def test_choose_reference(self):
        rc = DEFAULT_RC
        self.assertEqual(choose_reference([300, 305, 310], rc), (300, 300, []))
        # lone cheap offer: the floor stays the market price, 'typical' is the next corroborated one
        self.assertEqual(choose_reference([300, 340, 345], rc), (300, 340, ["lone_cheap_offer"]))
        self.assertEqual(choose_reference([300], rc), (300, 300, ["single_offer"]))
        self.assertEqual(choose_reference([], rc)[2], ["no_comparable_offer"])
        # percentage guard: on a EUR1500 phone a EUR20 gap is normal, not a lone outlier
        self.assertEqual(choose_reference([1500, 1520], rc)[2], [])

    def test_new_battery_buybox_is_replaced_by_optimal_twin(self):
        bat = ['<option value="/p/m/1b/?offer=1" selected="selected">New</option>'
               '<optgroup label="Available in other configurations"><option value="/p/m/1b/?offer=9" data-price="less,-€50">Optimal</option></optgroup>']
        pages = {"/p/m/1b/": page("1b", 500, battery="new", batteries=bat),
                 "/p/m/1b/?offer=9": page("1b", 450, battery="optimal", seller="Shop B")}
        r = scraper_with(pages)
        res = r._combo("m", "1", "b", EXPECT)
        self.assertEqual(res["price"], 450.0)
        self.assertEqual(res["offers"][0]["seller"], "Shop B")
        self.assertNotIn("new_battery", res["excluded"])

    def test_only_new_battery_means_no_comparable_offer(self):
        pages = {"/p/m/1b/": page("1b", 500, battery="new")}
        res = scraper_with(pages)._combo("m", "1", "b", EXPECT)
        self.assertIsNone(res["price"])
        self.assertIn("no_comparable_offer", res["flags"])
        self.assertEqual(res["excluded"], {"new_battery": 1})

    def test_esim_only_offer_is_excluded_and_next_colour_used(self):
        cols = [("white", 20, "/p/m/2b/?offer=2")]
        pages = {"/p/m/1b/": page("1b", 400, sim="Dual-SIM (2 eSIMs)", colours=cols),
                 "/p/m/2b/?offer=2": page("2b", 420, colour="white")}
        res = scraper_with(pages)._combo("m", "1", "b", EXPECT)
        self.assertEqual(res["price"], 420.0)
        self.assertEqual(res["excluded"], {"esim_only": 1})

    def test_cheapest_colours_are_verified_and_lone_offer_flagged(self):
        cols = [("white", 40, "/p/m/2b/?o=2"), ("red", 45, "/p/m/3b/?o=3"), ("blue", 300, "/p/m/4b/?o=4")]
        pages = {"/p/m/1b/": page("1b", 300, colours=cols),
                 "/p/m/2b/?o=2": page("2b", 340, colour="white"),
                 "/p/m/3b/?o=3": page("3b", 345, colour="red"),
                 "/p/m/4b/?o=4": page("4b", 600, colour="blue")}
        r = scraper_with(pages)
        res = r._combo("m", "1", "b", EXPECT)
        self.assertEqual((res["price"], res["floor"], res["typical"]), (300.0, 300.0, 340.0))
        self.assertIn("lone_cheap_offer", res["flags"])
        self.assertNotIn("/p/m/4b/?o=4", r.client.requested)          # dearest colour never needed

    def test_wrong_grade_page_is_rejected(self):
        pages = {"/p/m/1b/": page("1b", 300)}
        self.assertIsNone(scraper_with(pages)._combo("m", "1", "b", ("Good", "128GB")))

    def test_redirected_page_is_rejected(self):
        pages = {"/p/m/1b/": page("9b", 300)}      # asked for 1b, page is for 9b
        self.assertIsNone(scraper_with(pages)._combo("m", "1", "b", EXPECT))

    def test_engine_uses_floor_as_market_price_and_exposes_evidence(self):
        ref = {("Good", "128GB"): {"price": 300.0, "floor": 300.0, "typical": 340.0, "in_stock": True, "url": "https://x",
                                   "offers": [{"price": 300}], "excluded": {"new_battery": 1}, "flags": ["lone_cheap_offer"]}}
        x = build_model({("Good", "128GB"): v(449.99)}, ref, P)[0]
        self.assertEqual((x["ref_price"], x["ref_typical"]), (300.0, 340.0))
        self.assertEqual(x["delta"], 149.99)                       # the gap shown is to the real floor
        self.assertIn("lone_cheap_offer", x["flags"])
        self.assertEqual(x["ref_excluded"], {"new_battery": 1})

    def test_never_cuts_price_to_chase_a_lone_offer(self):
        ref = {("Good", "128GB"): {"price": 300.0, "floor": 300.0, "typical": 440.0, "in_stock": True, "url": "https://x",
                                   "offers": [], "excluded": {}, "flags": ["lone_cheap_offer"]}}
        x = build_model({("Good", "128GB"): v(449.99)}, ref, P)[0]
        self.assertEqual(x["status"], "refurbed_cheaper")          # we are still shown as out of line
        self.assertGreaterEqual(x["recommended"]["price"], 429.99)  # ...but the cut targets the corroborated price (440)
        # and if we're already at the corroborated price there is nothing to do
        y = build_model({("Good", "128GB"): v(439.99)}, ref, P)[0]
        self.assertIsNone(y["recommended"])

    def test_raise_never_prices_above_a_lone_cheap_offer(self):
        ref = {("Good", "128GB"): {"price": 409.0, "floor": 409.0, "typical": 453.99, "in_stock": True, "url": "https://x",
                                   "offers": [], "excluded": {}, "flags": ["lone_cheap_offer"]}}
        x = build_model({("Good", "128GB"): v(380)}, ref, P)[0]
        self.assertEqual(x["status"], "vs_cheaper")
        self.assertLess(x["recommended"]["price"], 409.0)

    def test_new_battery_only_price_is_kept_as_context(self):
        pages = {"/p/m/1b/": page("1b", 680, battery="new")}
        res = scraper_with(pages)._combo("m", "1", "b", EXPECT)
        self.assertEqual(res["excluded_from"], {"new_battery": 680.0})

    def test_engine_no_comparable_offer_is_no_data(self):
        ref = {("Good", "128GB"): {"price": None, "floor": None, "flags": ["no_comparable_offer"], "offers": [], "excluded": {"new_battery": 2}}}
        x = build_model({("Good", "128GB"): v(449.99)}, ref, P)[0]
        self.assertEqual(x["status"], "no_ref_data")
        self.assertIsNone(x["recommended"])
        self.assertIn("no_comparable_offer", x["flags"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------
# Phase 1: kinds, reasons, confidence, ladder limits, queue split, guards, export
# ---------------------------------------------------------------------------------------------
from refurbed_scraper import supported_price


def rref(price, sellers=2, supported=None, flags=(), typical=None):
    return {"price": price, "floor": price, "typical": typical if typical is not None else price,
            "supported": supported if supported is not None else price, "sellers": sellers,
            "in_stock": True, "url": "https://x", "flags": list(flags)}


def one(vs, ref):
    rows = build_model(vs, ref, P)
    return {(x["condition"], x["storage"]): x for x in rows}


class Phase1Engine(unittest.TestCase):
    def test_plain_market_move_has_kind_reason_and_high_confidence(self):
        row = one({("Good", "128GB"): v(400)}, {("Good", "128GB"): rref(350)})[("Good", "128GB")]
        rec = row["recommended"]
        self.assertEqual((rec["kind"], rec["direction"]), ("market", "down"))
        self.assertIn("€50", rec["reason"])
        self.assertEqual(row["confidence"]["level"], "high")
        self.assertEqual(row["queue"], "action")

    def test_missing_competitor_price_gives_no_recommendation_and_no_confidence(self):
        row = one({("Good", "128GB"): v(400)}, {})[("Good", "128GB")]
        self.assertIsNone(row["recommended"])
        self.assertEqual(row["confidence"]["level"], "none")
        self.assertEqual(row["queue"], "none")

    def test_big_cut_is_labelled_capped_and_shows_ideal(self):
        row = one({("Good", "128GB"): v(600)}, {("Good", "128GB"): rref(300)})[("Good", "128GB")]
        rec = row["recommended"]
        self.assertEqual(rec["kind"], "manual_review")       # 100% gap -> unusual, review first
        self.assertTrue({"review_large_gap"} <= set(row["flags"]))
        row = one({("Good", "128GB"): v(300)}, {("Good", "128GB"): rref(400)})[("Good", "128GB")]
        self.assertEqual(row["recommended"]["kind"], "capped")
        self.assertGreater(row["recommended"]["ideal_price"], row["recommended"]["price"])
        self.assertIn("limited", row["recommended"]["reason"])
        self.assertEqual(row["confidence"]["level"], "high")

    def test_extreme_gap_never_becomes_a_plain_market_move(self):
        for ours, theirs in ((900, 200), (100, 500)):
            row = one({("Good", "128GB"): v(ours)}, {("Good", "128GB"): rref(theirs)})[("Good", "128GB")]
            if row["recommended"]:
                self.assertEqual(row["recommended"]["kind"], "manual_review")
            self.assertEqual(row["confidence"]["level"], "low")

    def test_same_seller_and_unverified_spec_lower_confidence_but_keep_the_cheapest_price(self):
        row = one({("Good", "128GB"): v(400)},
                  {("Good", "128GB"): rref(350, sellers=1, supported=None, flags=["same_seller_only", "spec_unverified"])})[("Good", "128GB")]
        self.assertEqual(row["ref_price"], 350)                       # cheapest offer still the comparison
        self.assertEqual(row["confidence"]["level"], "medium")
        self.assertTrue(any("one seller" in m for m in row["confidence"]["missing"]))
        self.assertTrue(any("SIM" in m for m in row["confidence"]["missing"]))

    def test_stale_or_single_offer_is_low_confidence(self):
        row = one({("Good", "128GB"): v(400)}, {("Good", "128GB"): rref(350, flags=["stale_data"])})[("Good", "128GB")]
        self.assertEqual(row["confidence"]["level"], "low")
        row = one({("Good", "128GB"): v(400)}, {("Good", "128GB"): rref(350, flags=["single_offer"], supported=None)})[("Good", "128GB")]
        self.assertEqual(row["confidence"]["level"], "low")

    def test_ladder_raise_obeys_raise_limit_and_becomes_manual_review(self):
        # 256GB is priced BELOW 128GB, and no Refurbed data: the old engine raised it by 100%+.
        vs = {("Good", "128GB"): v(400), ("Good", "256GB"): v(200)}
        row = one(vs, {})[("Good", "256GB")]
        rec = row["recommended"]
        self.assertEqual(rec["kind"], "manual_review")
        self.assertIn("ladder_unresolved", row["flags"])
        self.assertGreater(rec["price"], 200 * 1.1 + 5)           # the price the ladder WANTS ...
        self.assertLess(rec["limit_price"], rec["price"])         # ... is clearly marked as beyond the limit
        self.assertIn("raise limit", rec["reason"])

    def test_blocked_ladder_keeps_the_market_price_visible_not_the_ladder_price(self):
        vs = {("Good", "128GB"): v(500), ("V. Good", "128GB"): v(520)}
        ref = {("V. Good", "128GB"): rref(450)}
        row = one(vs, ref)[("V. Good", "128GB")]
        rec = row["recommended"]
        self.assertEqual(rec["kind"], "manual_review")
        self.assertLess(rec["price"], 520)                       # still the market cut ...
        self.assertGreater(rec["ladder_wants"], rec["price"])    # ... with the ladder's demand recorded
        self.assertIn("ladder_unresolved", row["flags"])

    def test_small_ladder_fix_is_applied_and_labelled_ladder(self):
        vs = {("Good", "128GB"): v(300), ("V. Good", "128GB"): v(305)}
        row = one(vs, {})[("V. Good", "128GB")]
        self.assertEqual(row["recommended"]["kind"], "ladder")
        self.assertEqual(row["recommended"]["price"], 319.99)
        self.assertIn("Good is", row["recommended"]["reason"])

    def test_ladder_never_pushes_a_price_above_refurbed_plus_tolerance(self):
        vs = {("Good", "128GB"): v(300), ("V. Good", "128GB"): v(305)}
        ref = {("V. Good", "128GB"): rref(306)}
        row = one(vs, ref)[("V. Good", "128GB")]
        self.assertEqual(row["recommended"]["kind"], "manual_review")     # ladder wants 319.99, market allows < 316
        self.assertIn("Refurbed price", row["recommended"]["reason"])

    def test_no_recommendation_exceeds_limits_unless_manual_review(self):
        import random
        rnd = random.Random(7)
        for _ in range(300):
            vs, ref = {}, {}
            for st in ("64GB", "128GB", "256GB"):
                for c in engine.CONDITIONS:
                    if rnd.random() < .8:
                        vs[(c, st)] = v(rnd.choice(range(100, 900, 10)) + .99)
                    if rnd.random() < .6:
                        ref[(c, st)] = rref(rnd.choice(range(100, 900, 10)) + .99)
            for row in build_model(vs, ref, P):
                rec = row["recommended"]
                if rec and rec["kind"] != "manual_review":
                    up = rec["price"] - row["vs_price"]
                    self.assertLessEqual(up, row["vs_price"] * P.max_raise_pct / 100 + 0.01, row)
                    self.assertGreaterEqual(rec["price"], row["vs_price"] * (1 - P.max_lower_pct / 100) - 10.01, row)

    def test_out_of_stock_goes_to_maintenance_queue(self):
        rows = one({("Good", "128GB"): v(400, stock=False), ("V. Good", "128GB"): v(400)},
                   {("Good", "128GB"): rref(350), ("V. Good", "128GB"): rref(350)})
        self.assertEqual(rows[("Good", "128GB")]["queue"], "maintenance")
        self.assertEqual(rows[("V. Good", "128GB")]["queue"], "action")

    def test_guard_hook_can_block_a_cut_without_touching_the_engine(self):
        def margin_guard(row, policy):               # stand-in for a future cost-sheet guard
            rec = row["recommended"]
            if rec and rec["direction"] == "down":
                row["flags"].append("below_min_margin")
                rec["kind"], rec["reason"] = "manual_review", "Cut would take margin under the minimum."
        rows = build_model({("Good", "128GB"): v(400)}, {("Good", "128GB"): rref(350)}, P, guards=[margin_guard])
        self.assertEqual(rows[0]["recommended"]["kind"], "manual_review")
        self.assertIn("below_min_margin", rows[0]["flags"])

    def test_summary_counts_kinds_and_queues(self):
        rows = build_model({("Good", "128GB"): v(400), ("V. Good", "128GB"): v(400, stock=False)},
                           {("Good", "128GB"): rref(350), ("V. Good", "128GB"): rref(350)}, P)
        s = engine.summarise([{"brand": "apple", "model": "X", "variants": rows}])
        self.assertEqual((s["queue"], s["maintenance"]), (1, 1))
        self.assertEqual(s["by_kind"]["market"], 1)


class Phase1Scraper(unittest.TestCase):
    RC = dict(DEFAULT_RC)

    def test_two_colours_from_one_seller_are_not_independent_support(self):
        offers = [{"price": 300, "seller": "A"}, {"price": 305, "seller": "A"}, {"price": 310, "seller": "A"}]
        self.assertIsNone(supported_price(offers, self.RC))

    def test_two_sellers_close_together_support_the_lower_price(self):
        offers = [{"price": 300, "seller": "A"}, {"price": 305, "seller": "B"}, {"price": 400, "seller": "C"}]
        self.assertEqual(supported_price(offers, self.RC), 300)

    def test_cheap_lone_seller_is_skipped_for_support_but_stays_the_floor(self):
        offers = [{"price": 250, "seller": "A"}, {"price": 300, "seller": "B"}, {"price": 305, "seller": "C"}]
        self.assertEqual(supported_price(offers, self.RC), 300)

    def test_unnamed_sellers_never_count_as_independent(self):
        self.assertIsNone(supported_price([{"price": 300, "seller": None}, {"price": 301, "seller": None}], self.RC))

    def test_missing_sim_or_battery_marks_offer_spec_unverified(self):
        pages = {"/p/m/s1b/": page("s1b", 300, sim=None, seller="A")}
        sc = scraper_with(pages)
        pg = parse_variant_page(pages["/p/m/s1b/"])
        res = sc.evaluate("/p/m/s1b/", pg, pages["/p/m/s1b/"], {})
        self.assertIn("spec_unverified", res["flags"])
        self.assertFalse(res["offers"][0]["spec_verified"])


class ExportModule(unittest.TestCase):
    def test_node_export_tests_pass(self):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        res = subprocess.run([node, "--test", os.path.join(HERE, "export_test.js")], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout[-2000:] + res.stderr[-1000:])


class CostModule(unittest.TestCase):
    def test_node_cost_tests_pass(self):
        import shutil, subprocess, tempfile
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        env = dict(os.environ)
        try:
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sept"
            ws.append(["REF", "MAKE", "MODEL", "PRICE", "ROE", 1.99, "VSG Cost"])
            ws.append([18598, "Apple", "iPhone 15 Pro 128GB", 401.84, None, None, 342.22])
            ws.append([18599, "Apple", "iPhone 15 Pro Max 256GB", 455.0, None, None, 386.83])
            ws.append([6764, "Apple", "iPhone 4 16GB", 0, None, None, 5])
            f = os.path.join(tempfile.mkdtemp(), "book.xlsx"); wb.save(f); env["PRICEBOOK_FIXTURE"] = f
        except ImportError:
            pass
        res = subprocess.run([node, "--test", os.path.join(HERE, "costs_test.js")], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0, res.stdout[-3000:] + res.stderr[-1000:])
