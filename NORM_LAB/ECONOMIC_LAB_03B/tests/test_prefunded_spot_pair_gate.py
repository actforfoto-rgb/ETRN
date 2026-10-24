"""Independent public Bitget/HTX L2 economic and causal timestamp tests."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from decimal import Decimal as D

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prefunded_spot_pair_gate as x


def books(asset="LTC",buy_offer="60",sell_bid="61",age_b=100,
          age_s=110, skew_receipt=70, quantity="100", sell_quantity="100"):
    rb=1_500_000;rs=rb+skew_receipt
    a={"venue":"BITGET","asset":asset,"received_utc_ms":rb,
       "started_utc_ms":rb-150,"exchange_utc_ms":rb-age_b,
       "body_sha256":"book_source_a",
       "bids":[(D("59.9"),D(quantity))],
       "asks":[(D(buy_offer),D(quantity))]}
    b={"venue":"HTX","asset":asset,"received_utc_ms":rs,
       "started_utc_ms":rs-150,"exchange_utc_ms":rs-age_s,
       "body_sha256":"book_source_b",
       "bids":[(D(sell_bid),D(sell_quantity))],
       "asks":[(D("61.1"),D("100"))]}
    return a,b


class PairQuoteTests(unittest.TestCase):
    def test_positive_requires_fresh_both_books_full_depth_costed(self):
        a,b=books()
        r=x.evaluate(a,b,D(1000))
        self.assertEqual(r["status"],"PUBLIC_QUOTE_POSITIVE_UNVERIFIED")
        self.assertGreater(D(r["net_after_model_costs_usdt"]),D(0))
        self.assertEqual(r["real_orders"],0)
        self.assertFalse(r["owner_inventories_verified"])
        self.assertFalse(r["owner_account_permission_verified"])
        self.assertEqual(r["quote_not_execution"],True)

    def test_small_spread_is_negative_after_fee_and_rebalance(self):
        a,b=books(sell_bid="60.2")
        b["asks"]=[(D("60.3"),D("100"))]
        r=x.evaluate(a,b,D(1000))
        self.assertEqual(r["status"],"NO_POSITIVE_NET_AFTER_FULL_DEPTH")
        self.assertLess(D(r["net_after_model_costs_usdt"]),0)

    def test_stale_bitget_quote_fails_closed(self):
        a,b=books(age_b=3500)
        r=x.evaluate(a,b,D(1000))
        self.assertEqual(r["status"],"REJECT_CAUSAL_TIMING")
        self.assertEqual(r["pair_gate"]["status"],"STALE_OR_FUTURE_BOOK")

    def test_stale_htx_quote_fails_closed(self):
        a,b=books(age_s=3500)
        self.assertEqual(x.book_pair_gate(a,b)["status"],"STALE_OR_FUTURE_BOOK")

    def test_future_book_fails_closed(self):
        a,b=books(age_s=-400)
        self.assertEqual(x.book_pair_gate(a,b)["status"],"STALE_OR_FUTURE_BOOK")

    def test_book_market_timestamp_skew_fails_closed(self):
        a,b=books(age_s=1500)
        self.assertEqual(x.book_pair_gate(a,b)["status"],"NOT_SIMULTANEOUS_L2")

    def test_receipt_gap_fails_closed(self):
        a,b=books(skew_receipt=2100,age_b=100,age_s=100)
        self.assertEqual(x.book_pair_gate(a,b)["status"],"NOT_SIMULTANEOUS_L2")

    def test_not_cross_asset_even_if_price_profits(self):
        a,b=books()
        b["asset"]="FIL"
        self.assertNotEqual(x.book_pair_gate(a,b)["status"],"SYNCHRONIZED_PUBLIC_QUOTE")

    def test_partial_htx_bid_depth_rejects(self):
        a,b=books(sell_quantity="2")
        r=x.evaluate(a,b,D(1000))
        self.assertEqual(r["status"],"REJECT_SELL_DEPTH")

    def test_missing_buy_depth_rejects(self):
        a,b=books(quantity="2")
        self.assertEqual(x.evaluate(a,b,D(1000))["status"],"REJECT_BUY_DEPTH")

    def test_correct_bitget_data_ts_mandatory(self):
        payload={"code":"00000","requestTime":1_500_000,
            "data":{"ts":"1499980","a":[["60","50"],["60.1","90"]],
                    "b":[["59.9","50"],["59.8","90"]]}}
        rc={"venue":"BITGET","asset":"LTC",
            "received_utc_ms":1_500_080,"started_utc_ms":1_499_850,"body_sha256":"a"}
        q=x.parse_book("BITGET","LTC",payload,rc)
        self.assertEqual(q["exchange_utc_ms"],1499980)
        self.assertEqual(q["asks"][0][0],D("60"))

    def test_correct_htx_tick_ts_mandatory(self):
        payload={"status":"ok","ts":1_500_060,
            "tick":{"ts":1499990,"bids":[[61,50],[60.9,50]],
            "asks":[[61.1,50],[61.2,50]]}}
        rc={"venue":"HTX","asset":"LTC",
            "received_utc_ms":1500100,"started_utc_ms":1499900,"body_sha256":"b"}
        q=x.parse_book("HTX","LTC",payload,rc)
        self.assertEqual(q["exchange_utc_ms"],1499990)

    def test_crossed_venue_book_rejected(self):
        payload={"code":"00000","data":{"ts":"1500000",
                 "a":[["60","10"]],"b":[["61","20"]]}}
        rc={"venue":"BITGET","asset":"LTC",
            "received_utc_ms":1500000,"started_utc_ms":1499900,"body_sha256":"a"}
        self.assertRaises(ValueError,x.parse_book,"BITGET","LTC",payload,rc)

    def test_unsorted_book_rejected(self):
        payload={"status":"ok","ts":1500000,"tick":{"ts":1500000,
                "bids":[[60,10],[61,10]],"asks":[[62,10],[63,10]]}}
        rc={"venue":"HTX","asset":"LTC","received_utc_ms":1500000,
            "started_utc_ms":1499900,"body_sha256":"a"}
        self.assertRaises(ValueError,x.parse_book,"HTX","LTC",payload,rc)

    def test_requires_exactly_two_distinct_venues(self):
        a,b=books()
        b["venue"]="BITGET"
        self.assertNotEqual(x.book_pair_gate(a,b)["status"],"SYNCHRONIZED_PUBLIC_QUOTE")

    def test_no_order_paths_and_conservative_two_leg_extra_cost(self):
        self.assertFalse(hasattr(x,"place_order"))
        self.assertFalse(hasattr(x,"send_order"))
        self.assertFalse(hasattr(x,"create_order"))
        a,b=books()
        r=x.evaluate(a,b,D(1000))
        self.assertGreater(D(r["extra_5bp_each_leg_usdt"]),D(1))
        self.assertEqual(D(r["rebalance_reserve_scenario_usdt"]),D("1"))

    def test_invalid_observe_parameters(self):
        self.assertRaises(ValueError,x.observe,0)
        self.assertRaises(ValueError,x.observe,5)
        self.assertRaises(ValueError,x.observe,1,11)

if __name__=="__main__":
    unittest.main()
