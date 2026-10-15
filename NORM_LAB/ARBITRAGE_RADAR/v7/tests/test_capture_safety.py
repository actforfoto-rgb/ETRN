"""Boundary regression tests for the read-only Finam WebSocket recorder."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from finam_ws_collector import capture_stream, sanitize_envelope

SYMBOL = "CRZ6CRH7@RTSX"
DATA = {"type": "DATA", "subscription_type": "ORDER_BOOK", "timestamp": 1792080000,
        "payload": {"order_book": [{"symbol": SYMBOL, "rows": [
            {"price": "0.196", "buy_size": "8", "sell_size": "0"}]}]}}


class MockSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    async def recv(self):
        if self.messages:
            return json.dumps(self.messages.pop(0))
        await asyncio.sleep(5)

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class MockConnect:
    def __init__(self, socket):
        self.socket = socket

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *args):
        return False


class CaptureSafetyTests(unittest.TestCase):
    def test_premature_market_data_is_not_recorded_as_complete(self):
        socket = MockSocket([
            DATA, {"type": "EVENT", "event_info": {"event": "HANDSHAKE_SUCCESS"}}, DATA
        ])
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "premature.jsonl"
            summary = asyncio.run(capture_stream("ephemeral-jwt", (SYMBOL,), 1, p, connector=MockConnect(socket)))
            self.assertEqual(summary["market_messages"], 1)
            self.assertEqual(summary["errors"], 1)
            self.assertEqual(summary["status"], "DATA_RECORDED_WITH_ERRORS_UNVERIFIED")
            self.assertEqual(len(p.read_text().splitlines()), 2)
            self.assertNotIn("ephemeral-jwt", p.read_text())

    def test_evidence_record_limit_marks_incomplete(self):
        socket = MockSocket([
            {"type": "EVENT", "event_info": {"event": "HANDSHAKE_SUCCESS"}}, DATA
        ])
        with tempfile.TemporaryDirectory() as td, patch("finam_ws_collector.MAX_EVIDENCE_RECORDS", 1):
            p = Path(td) / "capped.jsonl"
            s = asyncio.run(capture_stream("jwt", (SYMBOL,), 1, p, connector=MockConnect(socket)))
            self.assertEqual(s["status"], "BOUNDED_OUT")
            self.assertTrue(s["limits_reached"])
            self.assertEqual(s["records"], 1)
            self.assertEqual(s["market_messages"], 0)

    def test_byte_limit_marks_incomplete(self):
        socket = MockSocket([{"type": "EVENT", "event_info": {"event": "HANDSHAKE_SUCCESS"}}])
        with tempfile.TemporaryDirectory() as td, patch("finam_ws_collector.MAX_EVIDENCE_BYTES", 4):
            s = asyncio.run(capture_stream("jwt", (SYMBOL,), 1,
                                          Path(td) / "bytes.jsonl", connector=MockConnect(socket)))
            self.assertEqual(s["status"], "BOUNDED_OUT")
            self.assertTrue(s["limits_reached"])
            self.assertEqual(s["bytes"], 0)

    def test_boolean_nan_and_float_timestamp_not_accepted(self):
        message = dict(DATA)
        message["timestamp"] = True
        self.assertIsNone(sanitize_envelope(message, (SYMBOL,))["provider_timestamp"])
        message["timestamp"] = float("nan")
        self.assertIsNone(sanitize_envelope(message, (SYMBOL,))["provider_timestamp"])
        message["timestamp"] = 1792080000
        self.assertEqual(sanitize_envelope(message, (SYMBOL,))["provider_timestamp"], 1792080000)


if __name__ == "__main__":
    unittest.main()
