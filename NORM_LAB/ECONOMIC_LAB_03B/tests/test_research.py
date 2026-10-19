import json
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import Book, Screen, crc_book, parse_ns


def ts(ns):
    # Truncate tests to microseconds like exchange timestamps.
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


def book_frame(symbol, ns, bid="100", ask="101", corrupt=False, kind="snapshot"):
    bids = {Decimal(bid): Decimal("2")}
    asks = {Decimal(ask): Decimal("3")}
    item = {"symbol": symbol, "timestamp": ts(ns),
            "bids": [{"price": bid, "qty": "2"}],
            "asks": [{"price": ask, "qty": "3"}],
            "checksum": crc_book(bids, asks) + int(corrupt)}
    return json.dumps({"channel": "book", "type": kind, "data": [item]})


def trade_frame(ns, price="99", trade_id=15, side="sell"):
    return json.dumps({"channel": "trade", "type": "update", "data": [
        {"symbol": "BTC/USD", "timestamp": ts(ns), "trade_id": trade_id,
         "side": side, "price": price, "qty": "0.5"}]})


class ResearchTests(unittest.TestCase):
    def test_official_kraken_book_crc_vector(self):
        # Official Kraken WS v2 L2 checksum guide, expected 3310070434.
        a = [("45285.2", "0.00100000"), ("45286.4", "1.54571953"),
             ("45286.6", "1.54571109"), ("45289.6", "1.54560911"),
             ("45290.2", "0.15890660"), ("45291.8", "1.54553491"),
             ("45294.7", "0.04454749"), ("45296.1", "0.35380000"),
             ("45297.5", "0.09945542"), ("45299.5", "0.18772827")]
        b = [("45283.5", "0.10000000"), ("45283.4", "1.54582015"),
             ("45282.1", "0.10000000"), ("45281.0", "0.10000000"),
             ("45280.3", "1.54592586"), ("45279.0", "0.07990000"),
             ("45277.6", "0.03310103"), ("45277.5", "0.30000000"),
             ("45277.3", "1.54602737"), ("45276.6", "0.15445238")]
        asks = {Decimal(k): Decimal(v) for k, v in a}
        bids = {Decimal(k): Decimal(v) for k, v in b}
        self.assertEqual(crc_book(bids, asks), 3310070434)

    def test_touch_is_not_maker_fill_but_trade_through_proxy_is(self):
        s = Screen(["BTC/USD"])
        t0 = 1_800_000_000_000_000_000
        s.process(book_frame("BTC/USD", t0), t0)
        self.assertEqual(s.episodes["BTC/USD"].state, "LATENCY_PENDING")
        s.process(book_frame("BTC/USD", t0+500_000_000, kind="update"), t0+500_000_000)
        self.assertEqual(s.episodes["BTC/USD"].state, "POSTED_PROXY")
        s.process(trade_frame(t0+900_000_000, price="100", trade_id=1), t0+900_000_000)
        self.assertEqual(s.episodes["BTC/USD"].state, "POSTED_PROXY")
        s.process(trade_frame(t0+1_000_000_000, price="99", trade_id=2), t0+1_000_000_000)
        self.assertEqual(s.episodes["BTC/USD"].state, "FILLED_PROXY")
        s.process(book_frame("BTC/USD", t0+12_000_000_000, bid="100.5", kind="snapshot"), t0+12_000_000_000)
        out = s.finish()
        self.assertEqual(out["closed_proxy_cycles"], 1)
        self.assertLess(Decimal(out["closed_proxy_net_sum_usd"]), 0)
        self.assertEqual(out["status"], "MODEL_SCREEN_NOT_EXECUTABLE")
        self.assertFalse(out["fill_guarantee"])

    def test_corrupt_checksum_invalidates_book_and_episode(self):
        s = Screen(["BTC/USD"])
        t0 = 1_800_000_000_000_000_000
        s.process(book_frame("BTC/USD", t0), t0)
        s.process(book_frame("BTC/USD", t0+1_000_000_000, corrupt=True, kind="update"), t0+1_000_000_000)
        self.assertFalse(s.books["BTC/USD"].ready)
        self.assertEqual(s.finish()["state_counts"].get("INVALID_BOOK"), 1)

    def test_out_of_order_receive_fails_causal_check(self):
        s = Screen(["BTC/USD"])
        t0 = 1_800_000_000_000_000_000
        s.process(book_frame("BTC/USD", t0), t0)
        s.process(book_frame("BTC/USD", t0), t0-10)
        self.assertTrue(s.finish()["causal_receive_order_error"])

    def test_trade_before_posting_never_fills(self):
        s = Screen(["BTC/USD"])
        t0 = 1_800_000_000_000_000_000
        s.process(book_frame("BTC/USD", t0), t0)
        s.process(book_frame("BTC/USD", t0+500_000_000, kind="update"), t0+500_000_000)
        # Exchange timestamp predates order posting; delayed reception is not causality.
        s.process(trade_frame(t0+200_000_000, price="99", trade_id=3), t0+800_000_000)
        self.assertEqual(s.episodes["BTC/USD"].state, "POSTED_PROXY")

    def test_unknown_channel_is_not_trading_data(self):
        s = Screen(["BTC/USD"])
        s.process(json.dumps({"channel":"balances", "data":[{"symbol":"BTC/USD","money":1000}]}), 1800000000000000000)
        out = s.finish()
        self.assertEqual(out["closed_proxy_cycles"], 0)
        self.assertEqual(out["book_valid_updates"], 0)

if __name__ == '__main__':
    unittest.main()
