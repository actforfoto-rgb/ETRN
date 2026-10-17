"""Offline deterministic tests. Never require a real Finam secret or network."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finam_ws_collector import (
    ControlledFailure, authorize_readonly, capture_stream, sanitize_envelope,
    scrub_market_payload, validate_symbols,
)

SYMBOL = "CRZ6CRH7@RTSX"


class MockSocket:
    def __init__(self, events):
        self.events = list(events)
        self.sent = []

    async def recv(self):
        if self.events:
            return json.dumps(self.events.pop(0))
        await asyncio.sleep(10)

    async def send(self, data):
        self.sent.append(json.loads(data))


class MockConnect:
    def __init__(self, socket):
        self.socket = socket
    def __call__(self, *args, **kwargs):
        return self
    async def __aenter__(self):
        return self.socket
    async def __aexit__(self, typ, value, traceback):
        return False


class ReadOnlyAuthTests(unittest.TestCase):
    def test_rejects_missing_key(self):
        with self.assertRaisesRegex(ControlledFailure, "READONLY_SECRET_NOT_CONFIGURED"):
            authorize_readonly("")

    def test_rejects_non_readonly_session(self):
        responses = [
            {"token": "jwt-example-token-123"},
            {"readonly": False, "account_ids": ["internal-account"]},
        ]
        with patch("finam_ws_collector.broker_json", side_effect=responses) as mock_call:
            with self.assertRaisesRegex(ControlledFailure, "READONLY_SESSION_NOT_CONFIRMED"):
                authorize_readonly("tapi_mock_only")
            self.assertEqual(mock_call.call_count, 2)
            self.assertEqual(mock_call.call_args_list[0].args[0], "/v1/sessions")

    def test_rejects_accounts_without_rtsx_permission(self):
        responses = [
            {"token": "jwt-example-token-123"},
            {"readonly": True, "account_ids": ["internal-account"],
             "md_permissions": [{"mic": "MISX", "delay_minutes": 0, "quote_level": "LEVEL_2"}]},
        ]
        with patch("finam_ws_collector.broker_json", side_effect=responses):
            with self.assertRaisesRegex(ControlledFailure, "RTSX_REALTIME_PERMISSION_NOT_CONFIRMED"):
                authorize_readonly("tapi_mock_only")

    def test_accepts_one_readonly_account_and_rtsx_realtime(self):
        responses = [
            {"token": "jwt-example-token-123"},
            {"readonly": True, "account_ids": ["internal-account"],
             "md_permissions": [{"mic": "RTSX", "delay_minutes": 0, "quote_level": "LEVEL_2"}]},
        ]
        with patch("finam_ws_collector.broker_json", side_effect=responses):
            self.assertEqual(authorize_readonly("tapi_mock_only"), "jwt-example-token-123")


class ParserTests(unittest.TestCase):
    def test_bound_to_six_rtsx_contracts(self):
        self.assertEqual(validate_symbols(SYMBOL), (SYMBOL,))
        with self.assertRaises(ControlledFailure):
            validate_symbols("INVALID@MISX")
        with self.assertRaises(ControlledFailure):
            validate_symbols(",".join([SYMBOL] * 7))

    def test_scrubs_unexpected_sensitive_fields(self):
        msg = {"order_book": [{"symbol": SYMBOL, "token": "private-secret", "rows": [
            {"price": "-0.018", "buy_size": "13", "sell_size": "0",
             "timestamp": "2026-10-15T17:47:54Z", "account_id": "private-account", "action": "ACTION_ADD"}
        ]}], "user": "private-account"}
        clean = scrub_market_payload(msg)
        s = json.dumps(clean)
        self.assertNotIn("private-secret", s)
        self.assertNotIn("private-account", s)
        self.assertIn("-0.018", s)

    def test_market_only_not_account_or_orders(self):
        self.assertIsNone(sanitize_envelope({"type": "DATA", "subscription_type": "ACCOUNT", "payload": {}}, (SYMBOL,)))
        self.assertIsNone(sanitize_envelope({"type": "DATA", "subscription_type": "ORDERS", "payload": {}}, (SYMBOL,)))
        self.assertIsNone(sanitize_envelope({"type": "DATA", "subscription_type": "ORDER_BOOK", "payload": {"order_book": [{"symbol": "X@RTSX"}]}}, (SYMBOL,)))


class CollectorTests(unittest.TestCase):
    def test_subscribes_only_after_handshake_and_never_stores_jwt(self):
        ws = MockSocket([
            {"type": "EVENT", "event_info": {"event": "HANDSHAKE_SUCCESS"}},
            {"type": "DATA", "subscription_type": "ORDER_BOOK", "timestamp": 1792080000,
             "payload": {"order_book": [{"symbol": SYMBOL, "rows": [
                 {"price": "0.196", "buy_size": "4", "sell_size": "0"},
                 {"price": "0.198", "buy_size": "0", "sell_size": "5"}
             ]}]}},
        ])
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "mock.jsonl"
            summary = asyncio.run(capture_stream("secret-jwt", (SYMBOL,), 1, output, connector=MockConnect(ws)))
            self.assertTrue(summary["subscribed"])
            self.assertEqual(summary["market_messages"], 1)
            self.assertEqual(summary["orders"], 0)
            self.assertEqual(len(ws.sent), 1)
            self.assertEqual(ws.sent[0]["type"], "ORDER_BOOK")
            raw = output.read_text()
            self.assertNotIn("secret-jwt", raw)
            self.assertIn("0.196", raw)
            self.assertEqual(len(raw.splitlines()), 2)
            self.assertEqual(len(summary["sha256"]), 64)

    def test_no_handshake_no_subscription(self):
        ws = MockSocket([{"type": "EVENT", "event_info": {"event": "CONNECTION_CLOSED"}}])
        with tempfile.TemporaryDirectory() as td:
            summary = asyncio.run(capture_stream("secret-jwt", (SYMBOL,), 1, Path(td) / "events.jsonl", connector=MockConnect(ws)))
            self.assertFalse(summary["subscribed"])
            self.assertEqual(summary["status"], "NO_HANDSHAKE")
            self.assertEqual(len(ws.sent), 0)

    def test_fails_if_existing_evidence_would_be_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "exists.jsonl"
            p.write_text("original")
            with self.assertRaises(FileExistsError):
                asyncio.run(capture_stream("jwt", (SYMBOL,), 1, p, connector=MockConnect(MockSocket([]))))
            self.assertEqual(p.read_text(), "original")



class TradeSubscriptionSelectionTests(unittest.TestCase):
    def test_default_cli_market_prints_include_wheat(self):
        books=fc.DEFAULT_SYMBOLS
        self.assertEqual(fc.select_trade_symbols(books),fc.DEFAULT_TRADE_SYMBOLS)

    def test_explicit_subset_and_empty_disable(self):
        chosen=(SYMBOL,'W4V6W4X6@RTSX')
        self.assertEqual(fc.select_trade_symbols(chosen,'W4V6W4X6@RTSX'),('W4V6W4X6@RTSX',))
        self.assertEqual(fc.select_trade_symbols(chosen,''),())

    def test_other_book_default_does_not_subscribe_unrelated_trade_stream(self):
        self.assertEqual(fc.select_trade_symbols(('SRZ6SRH7@RTSX',)),())

    def test_trade_subscription_outside_books_is_rejected(self):
        with self.assertRaises(fc.ControlledFailure):
            fc.select_trade_symbols((SYMBOL,), 'SRZ6SRH7@RTSX')

if __name__ == "__main__":
    unittest.main()