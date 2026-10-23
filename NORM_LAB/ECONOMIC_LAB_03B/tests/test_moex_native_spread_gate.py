"""Offline tests for MOEX native-vs-synthetic spreads, including commission gate."""
import sys, unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import moex_native_spread_gate as g

def future(sec, short, base, margin, step, stepprice, fee):
    return {"SECID":sec,"SHORTNAME":short,"ASSETCODE":base,
        "LASTTRADEDATE":"2027-01-01","INITIALMARGIN":margin,
        "MINSTEP":step,"STEPPRICE":stepprice,"BUYSELLFEE":fee}

def quotes(bid,ask,upd="10:20:50"):
    return {"BID":bid,"OFFER":ask,"UPDATETIME":upd,
            "SYSTIME":"2026-10-23 10:35:50"}

class M1Tests(unittest.TestCase):
    def test_no_order_functions(self):
        self.assertFalse(hasattr(g,"place_order"))
        self.assertFalse(hasattr(g,"execute_trade"))
        self.assertFalse(hasattr(g,"fund_account"))

    def test_public_only(self):
        self.assertRaises(ValueError,g.fetch,"https://example.com")

    def test_name_identity(self):
        a=future("NGZ6","NG-12.26","NG",7310.12,.001,8.11255,3.91)
        b=future("NGF7","NG-1.27","NG",7216.24,.001,8.11255,3.60)
        self.assertEqual(g.spread_name(a,b),"NG-12.26-1.27")
        self.assertIsNone(g.spread_name(a,{**b,"ASSETCODE":"BRM"}))

    def test_synthetic_quote_orientation(self):
        p=g.synthetic_prices("3.50","3.51","3.7","3.71")
        self.assertEqual(p["synthetic_bid"],D(".19"))
        self.assertEqual(p["synthetic_ask"],D(".21"))

    def test_cross_not_actual_fill(self):
        a=future("NGZ6","NG-12.26","NG",7310.12,.001,8.11255,3.91)
        b=future("NGF7","NG-1.27","NG",7216.24,.001,8.11255,3.60)
        spread={"bid":D("-.29"),"ask":D("-.28")}
        # Synthetic bid far.bid - near.ask = -.272, cross vs native.ask -.28 = .008.
        r=g.evaluate(spread,a,b,quotes("3.850","3.851"),quotes("3.579","3.581"))
        self.assertEqual(D(r["cross_points"]),D(".008"))
        self.assertEqual(D(r["gross_rub_per_equal_contract_set"]),D("64.90040"))
        self.assertEqual(D(r["exchange_fee_four_fills_est_rub"]),D("15.02"))
        self.assertEqual(D(r["indicative_after_exchange_fee_rub"]),D("49.88040"))
        self.assertTrue(r["within_40k_stress_cap"])
        self.assertFalse(r["paper_fill"])
        self.assertFalse(r["portfolio_executable"])

    def test_broker_fee_missing_no_claim(self):
        a=future("A","NG-12.26","NG",1000,.001,8.11255,5)
        b=future("B","NG-1.27","NG",1000,.001,8.11255,5)
        r=g.evaluate({"bid":D("-.29"),"ask":D("-.28")},
                     a,b,quotes("3.85","3.851"),quotes("3.579","3.581"))
        self.assertFalse(r["broker_tariff_verified"])
        self.assertFalse(r["causal_simultaneity_proven"])
        self.assertIsNone(r["native_html_exchange_timestamp"])

    def test_zero_and_crossed_books_rejected(self):
        self.assertIsNone(g.synthetic_prices("0","1","3","4"))
        self.assertIsNone(g.synthetic_prices("2","1","4","5"))

    def test_tick_money_mismatch_rejected(self):
        a=future("A","NG-12.26","NG",1000,.001,8.11255,4)
        b=future("B","NG-1.27","NG",1000,.01,8.11255,4)
        self.assertIsNone(g.evaluate({"bid":D("1"),"ask":D("1.1")},
            a,b,quotes("3.85","3.851"),quotes("4.95","5.05")))

    def test_html_native_quote_parsing_with_decimal_commas(self):
        s='<tr><td nowrap="nowrap">BRM-11.26-12.26</td><td>-4,55</td><td>-4,6</td><td>-4,51</td></tr>'
        q=g.native_quotes(s)
        self.assertEqual(q["BRM-11.26-12.26"]["bid"],D("-4.6"))
        self.assertEqual(q["BRM-11.26-12.26"]["ask"],D("-4.51"))

    def test_no_trade_account_always(self):
        a=future("NGZ6","NG-12.26","NG",7310.12,.001,8.11255,3.91)
        b=future("NGF7","NG-1.27","NG",7216.24,.001,8.11255,3.60)
        dataset={"securities":{"columns":list(a),"data":[list(a.values()),list(b.values())]},
             "marketdata":{"columns":["SECID","BID","OFFER","UPDATETIME","SYSTIME"],
                  "data":[["NGZ6",3.85,3.851,"10:20:50","2026-10-23 10:35:50"],
                          ["NGF7",3.579,3.581,"10:20:50","2026-10-23 10:35:50"]]}}
        site='<td nowrap="nowrap">NG-12.26-1.27</td><td>-0,29</td><td>-0,29</td><td>-0,28</td>'
        out=g.analyse(dataset,site)
        self.assertEqual(out["number_of_futures"],2)
        self.assertEqual(out["matched_native_vs_synthetic_comparisons"],1)
        self.assertEqual(out["paper_fills"],0)
        self.assertEqual(out["virtual_rub_equity"],"50000")
        self.assertEqual(out["closed_cycles"],0)

if __name__=="__main__":
    unittest.main()
