"""Proof-gate tests with synthetic data only; absolutely no Finam API traffic."""
from datetime import datetime, timedelta, timezone
import unittest

from book_replay import BookReplay, InvalidMarketData

SYM = "CRZ6CRH7@RTSX"
OTHER = "SRZ6SRH7@RTSX"
NOW = datetime(2026, 10, 15, 17, 48, 12, tzinfo=timezone.utc)


def row(price, buy, sell, at=NOW, action=None):
    payload = {
        "price": {"value": str(price)},
        "buy_size": {"value": str(buy)},
        "sell_size": {"value": str(sell)},
        "timestamp": at.isoformat(),
    }
    if action is not None:
        payload["action"] = action
    return payload


def rest(rows=None):
    return {"orderbook": {"rows": rows if rows is not None else [
        row("0.196", "18", "0"),
        row("0.198", "0", "82"),
    ]}}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.replay = BookReplay((SYM, OTHER))

    def test_bootstrap_two_sided_but_never_executable(self):
        s = self.replay.bootstrap_rest(SYM, rest(), NOW + timedelta(milliseconds=100))
        self.assertEqual(s["bid"], "0.196")
        self.assertEqual(s["ask"], "0.198")
        self.assertEqual(s["bid_qty"], "18")
        self.assertEqual(s["ask_qty"], "82")
        self.assertEqual(s["status"], "TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF")
        self.assertFalse(s["continuity_verified"])
        self.assertFalse(s["executable"])
        self.assertIsNone(s["realized_pnl"])

    def test_negative_spread_prices_are_valid(self):
        x = self.replay.bootstrap_rest(SYM, rest([
            row("-0.018", "13", "0"),
            row("-0.011", "0", "2"),
        ]), NOW)
        self.assertEqual(x["bid"], "-0.018")
        self.assertEqual(x["ask"], "-0.011")

    def test_old_level_timestamp_is_not_claimed_unfillable(self):
        s = self.replay.bootstrap_rest(SYM, rest([
            row("100", "1", "0", NOW - timedelta(minutes=10)),
            row("101", "0", "2", NOW - timedelta(seconds=1)),
        ]), NOW)
        self.assertEqual(s["status"], "LEVEL_TIMESTAMP_OLD_NOT_BOOK_STATUS")
        self.assertTrue(s["market_data_observed"])
        self.assertFalse(s["executable"])

    def test_one_sided_never_creates_artificial_quote(self):
        s = self.replay.bootstrap_rest(SYM, rest([row("0.198", 0, 3)]), NOW)
        self.assertEqual(s["status"], "ONE_SIDED")
        self.assertIsNone(s["bid"])

    def test_crossed_book_blocks_bootstrap(self):
        with self.assertRaisesRegex(InvalidMarketData, "CROSSED_OR_LOCKED_BOOK"):
            self.replay.bootstrap_rest(SYM, rest([row(103, 1, 0), row(102, 0, 1)]), NOW)
        self.assertEqual(self.replay.view(SYM)["status"],
                         "INVALIDATED_REQUIRES_REST_BOOTSTRAP")

    def test_missing_level_timestamp_never_assumes_fresh(self):
        r = rest()
        for item in r["orderbook"]["rows"]:
            item.pop("timestamp")
        s = self.replay.bootstrap_rest(SYM, r, NOW)
        self.assertEqual(s["status"], "LEVEL_TIMESTAMP_UNVERIFIED")

    def test_bad_size_never_becomes_valid_book(self):
        with self.assertRaises(InvalidMarketData):
            self.replay.bootstrap_rest(SYM, rest([row(1, -3, 0)]), NOW)

    def test_unapproved_symbol_rejected(self):
        with self.assertRaises(InvalidMarketData):
            self.replay.bootstrap_rest("SBER@MISX", rest(), NOW)


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.replay = BookReplay((SYM, OTHER))

    def boot(self):
        self.replay.bootstrap_rest(SYM, rest(), NOW)
        return self.replay

    def test_delta_without_bootstrap_not_a_snapshot(self):
        s = self.replay.apply_delta(SYM, {
            "rows": [row("0.197", "10", 0, action="ACTION_ADD")]
        }, NOW, 1792080000)
        self.assertEqual(s["status"], "UNBOOTSTRAPPED")
        self.assertEqual(s["updates"], 0)

    def test_add_and_update_best_bid(self):
        self.boot()
        s = self.replay.apply_delta(SYM, {
            "rows": [row("0.197", "9", 0, action="ACTION_ADD")]
        }, NOW + timedelta(milliseconds=250), 1792080000)
        self.assertEqual(s["bid"], "0.197")
        self.assertEqual(s["updates"], 1)
        self.assertFalse(s["continuity_verified"])
        self.assertFalse(s["executable"])

    def test_remove_level_makes_one_sided(self):
        self.boot()
        s = self.replay.apply_delta(SYM, {
            "rows": [{"price": {"value": "0.196"}, "buy_size": {"value":"0"}, "action": "ACTION_REMOVE"}]
        }, NOW, 1792080000)
        self.assertEqual(s["status"], "ONE_SIDED")

    def test_ambiguous_unspecified_action_invalidates_all_further_updates(self):
        self.boot()
        s = self.replay.apply_delta(SYM, {
            "rows": [row("0.197", "9", 0, action="ACTION_UNSPECIFIED")]
        }, NOW, 1792080000)
        self.assertEqual(s["status"], "INVALIDATED_REQUIRES_REST_BOOTSTRAP")
        later = self.replay.apply_delta(SYM, {
            "rows": [row("0.197", "9", 0, action="ACTION_ADD")]
        }, NOW, 1792080001)
        self.assertEqual(later["updates"], 0)

    def test_incomplete_quantity_cannot_zero_out_other_side(self):
        self.boot()
        bad = row("0.196", "2", 0, action="ACTION_UPDATE")
        bad.pop("sell_size")
        s = self.replay.apply_delta(SYM, {"rows": [bad]}, NOW, 1792080000)
        self.assertEqual(s["status"], "INVALIDATED_REQUIRES_REST_BOOTSTRAP")

    def test_out_of_order_envelope_invalidates(self):
        self.boot()
        d = {"rows": [row("0.197", "4", 0, action="ACTION_ADD")]}
        self.replay.apply_delta(SYM, d, NOW, 1792080002)
        result = self.replay.apply_delta(SYM, d, NOW, 1792080001)
        self.assertEqual(result["reason"], "OUT_OF_ORDER_PROVIDER_TIME")

    def test_crossed_update_does_not_report_trade_opportunity(self):
        self.boot()
        bad = self.replay.apply_delta(SYM, {
            "rows": [row("0.199", "5", 0, action="ACTION_ADD")]
        }, NOW, 1792080000)
        self.assertEqual(bad["status"], "INVALIDATED_REQUIRES_REST_BOOTSTRAP")
        self.assertFalse(bad["executable"])

    def test_stream_interruption_requires_new_rest_bootstrap(self):
        self.boot()
        self.replay.invalidate_all("WS_DISCONNECTED")
        self.assertEqual(self.replay.view(SYM)["reason"], "WS_DISCONNECTED")
        self.assertEqual(self.replay.view(OTHER)["status"], "INVALIDATED_REQUIRES_REST_BOOTSTRAP")
        recovered = self.replay.bootstrap_rest(SYM, rest(), NOW)
        self.assertEqual(recovered["status"], "TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF")

    def test_invalid_envelope_timestamp_blocks(self):
        self.boot()
        result = self.replay.apply_delta(SYM, {
            "rows": [row("0.197", "2", 0, action="ACTION_ADD")]
        }, NOW, True)
        self.assertEqual(result["reason"], "INVALID_ENVELOPE_TIMESTAMP")

    def test_unauthorized_market_symbol_rejected(self):
        with self.assertRaises(InvalidMarketData):
            self.replay.apply_delta("FAKE@RTSX", {"rows": []}, NOW)


if __name__ == "__main__":
    unittest.main()
