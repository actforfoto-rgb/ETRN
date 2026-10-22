"""Offline acceptance tests: no real market access or broker orders."""
import sys
from pathlib import Path
import unittest
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import arb_forward as x

def make(venue="Gate", ask="100", bid="99", clock=2_000_000, qty="1000", symbol="LINK"):
    return x.Book(symbol, venue,
        ((x.dec(bid), x.dec(qty)),),
        ((x.dec(ask), x.dec(qty)),),
        clock, clock + 50, "a"*64)

class Acceptance(unittest.TestCase):
    def test_account_is_not_mutated_and_no_order_function(self):
        self.assertFalse(hasattr(x, "send_order"))
        self.assertFalse(hasattr(x, "place_order"))
        self.assertFalse(hasattr(x, "mutate_balance"))

    def test_frozen_basket_rotation(self):
        self.assertEqual(len(x.SYMBOL_GROUPS),3)
        self.assertEqual(sum(len(g) for g in x.SYMBOL_GROUPS),30)
        self.assertEqual(len(set(x.SYMBOLS)),30)
        self.assertEqual(x.basket_for_utc_hour(0),x.basket_for_utc_hour(3))
        self.assertNotEqual(x.basket_for_utc_hour(0),x.basket_for_utc_hour(1))
        self.assertIn("BTC",x.basket_for_utc_hour(2))
        self.assertNotIn("BTC",x.basket_for_utc_hour(0))

    def test_allowed_urls(self):
        self.assertIn("market/books", x.url("LINK","OKX"))
        self.assertRaises(ValueError,x.url,"BAD","OKX")
        self.assertRaises(ValueError,x.url,"LINK","BadVenue")

    def test_parse_gate(self):
        b=x.parse("LINK","Gate",{"asks":[["100","2"]],"bids":[["99","5"]],"update":2000},2150,"b"*64)
        self.assertTrue(b.valid())

    def test_parse_bitget(self):
        b=x.parse("LINK","Bitget",{"code":"00000","data":{"a":[["101","2"]],"b":[["100","3"]],"ts":"2000"}},2100,"b"*64)
        self.assertTrue(b.valid())

    def test_parse_okx(self):
        b=x.parse("LINK","OKX",{"code":"0","data":[{"asks":[["101","2","0","2"]],"bids":[["100","3","0","1"]],"ts":"2000"}]},2100,"b"*64)
        self.assertTrue(b.valid())

    def test_invalid_or_stale_book(self):
        self.assertFalse(make(ask="99",bid="100").valid())
        self.assertFalse(x.Book("LINK","Gate",((x.dec("98"),x.dec(1)),),((x.dec("101"),x.dec(1)),),2000,10000,"a"*64).valid())

    def test_sorted_asks_and_bids(self):
        b=x.Book("LINK","Gate",((x.dec(100),x.dec(2)),(x.dec(101),x.dec(2))),
                 ((x.dec(103),x.dec(2)),),2000,2050,"a"*64)
        self.assertFalse(b.valid())

    def test_depth_insufficient(self):
        self.assertIsNone(x.walk(((x.dec(100),x.dec(1)),),x.dec(2)))
        self.assertEqual(x.walk(((x.dec(100),x.dec(1)),(x.dec(101),x.dec(1))),x.dec(2)),x.dec(201))

    def test_positive_gross_but_negative_full(self):
        a=make(venue="Gate",ask="100",bid="99")
        b=make(venue="Bitget",ask="101",bid="100.1")
        r=x.evaluate(a,b,x.dec(100))
        self.assertGreater(x.dec(r["gross_usdt"]),0)
        self.assertLess(x.dec(r["net_scenario_usdt"]),0)

    def test_positive_full_is_still_not_fill(self):
        a=make(venue="Gate",ask="100",bid="99")
        b=make(venue="Bitget",ask="105",bid="104")
        r=x.evaluate(a,b,x.dec(100))
        self.assertGreater(x.dec(r["net_scenario_usdt"]),0)
        self.assertEqual(r["model"],"UNEXECUTED_PAIRED_BOOK_SCREEN")
        self.assertGreater(x.dec(r["linked_usdt_equivalent"]),x.dec(200))

    def test_reject_temporal_skew_and_same_venue(self):
        a=make(venue="Gate")
        b=make(venue="Bitget",clock=2_002_000,ask="106",bid="105")
        self.assertIsNone(x.evaluate(a,b,x.dec(100)))
        self.assertIsNone(x.evaluate(a,a,x.dec(100)))

    def test_wrong_underlying_rejected(self):
        a=make(venue="Gate")
        b=make(venue="Bitget",symbol="BTC")
        self.assertIsNone(x.evaluate(a,b,x.dec(100)))

    def test_fee_stress_fixed(self):
        self.assertEqual(x.FEE["Gate"],Decimal("0.001"))
        self.assertEqual(x.REBALANCE_RATE,Decimal("0.002"))
        self.assertEqual(x.REBALANCE_FIXED,Decimal("0.5"))

if __name__=="__main__":
    unittest.main()
