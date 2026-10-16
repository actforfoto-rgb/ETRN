"""No network; tests deliberately avoid production broker accounts and costs."""
import unittest
from universe_discovery import infer_leg_symbols, discover_spread_triangles


def asset(symbol, **changes):
    return {"symbol":symbol,"mic":"RTSX","type":"SPREADS","is_archived":False,**changes}


class LegParserTests(unittest.TestCase):
    def test_root_two_characters_and_1_digit_year(self):
        r=infer_leg_symbols("CRZ6CRH7@RTSX")
        self.assertEqual((r.root,r.first_contract,r.second_contract),("CR","CRZ6","CRH7"))
        self.assertEqual(r.identity_status,"TICKER_PATTERN_ONLY_NOT_VERIFIED")

    def test_root_containing_digit(self):
        r=infer_leg_symbols("W4V6W4X6@RTSX")
        self.assertEqual((r.root,r.first_contract,r.second_contract),("W4","W4V6","W4X6"))

    def test_two_digit_year(self):
        r=infer_leg_symbols("GLH26GLM26@RTSX")
        self.assertEqual((r.first_contract,r.second_contract),("GLH26","GLM26"))

    def test_invalid_or_wrong_market_rejected(self):
        for s in ("X@MISX","W4V6W4Z6@MISX","W4W4@RTSX","xvv@RTSX", "CRZ6GLH7@RTSX"):
            self.assertIsNone(infer_leg_symbols(s),s)

    def test_same_contract_rejected(self):
        self.assertIsNone(infer_leg_symbols("CRZ6CRZ6@RTSX"))


class CatalogDiscoveryTests(unittest.TestCase):
    def test_three_complete_same_root(self):
        q=[asset("W4V6W4X6@RTSX"),asset("W4X6W4Z6@RTSX"),asset("W4V6W4Z6@RTSX")]
        r=discover_spread_triangles(q)
        self.assertEqual(r["complete_triple_patterns"],1)
        self.assertEqual(r["complete"][0]["direct"],"W4V6W4Z6@RTSX")
        self.assertEqual(r["real_orders"],0)
        self.assertIsNone(r["realized_pnl"])

    def test_six_known_snapshots_do_not_close_triangle(self):
        symbols=["CRZ6CRH7@RTSX","SRZ6SRH7@RTSX","GLH7GLM7@RTSX",
                 "UCZ6UCH7@RTSX","W4V6W4X6@RTSX","W4X6W4Z6@RTSX"]
        r=discover_spread_triangles([asset(s) for s in symbols])
        self.assertEqual(r["sequential_chains"],1)
        self.assertEqual(r["complete_triple_patterns"],0)
        self.assertEqual(r["missing_direct"][0]["query_symbol"],"W4V6W4Z6@RTSX")
        self.assertEqual(r["missing_direct"][0]["exists_on_exchange"],"UNKNOWN")

    def test_archived_instruments_not_used(self):
        r=discover_spread_triangles([asset("W4V6W4X6@RTSX",is_archived=True),asset("W4X6W4Z6@RTSX")])
        self.assertEqual(r["active_rtsx_native_spreads_parsed"],1)

    def test_non_spreads_and_misx_not_used(self):
        q=[asset("W4V6W4X6@RTSX",type="FUTURES"), asset("W4X6W4Z6@RTSX",mic="MISX")]
        r=discover_spread_triangles(q)
        self.assertEqual(r["active_rtsx_native_spreads_parsed"],0)

    def test_missing_is_archived_means_unverified(self):
        a=asset("W4V6W4X6@RTSX")
        a.pop("is_archived")
        self.assertEqual(discover_spread_triangles([a])["active_rtsx_native_spreads_parsed"],0)

    def test_duplicates_do_not_multiply_pattern(self):
        a=asset("W4V6W4X6@RTSX")
        r=discover_spread_triangles([a,a])
        self.assertEqual(r["active_rtsx_native_spreads_parsed"],1)
        self.assertEqual(r["rejections"]["DUPLICATE_SYMBOL"],1)

    def test_unparseable_not_fabricated(self):
        r=discover_spread_triangles([asset("WEIRDSPREAD@RTSX")])
        self.assertEqual(r["active_rtsx_native_spreads_parsed"],0)
        self.assertEqual(r["rejections"]["UNPARSEABLE_TICKER"],1)

    def test_output_is_bounded_but_counts_exact(self):
        q=[asset("W4V6W4X6@RTSX"),asset("W4X6W4Z6@RTSX"),asset("W4V6W4Z6@RTSX")]
        r=discover_spread_triangles(q,max_candidates=1)
        self.assertLessEqual(len(r["complete"]),1)
        with self.assertRaises(ValueError):discover_spread_triangles(q,max_candidates=0)


if __name__ == "__main__":
    unittest.main()