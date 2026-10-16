import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from G0_DIAG import audit


class DiagnosticTests(unittest.TestCase):
    def test_not_started_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            a = audit(Path(td))
        self.assertEqual(a["gate"], "NO_PROVEN_BROKER_MARKET_DATA")
        self.assertEqual(a["market_data_envelopes"], 0)

    def test_handshake_not_market_data(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "data"; d.mkdir()
            (d / "finam_ws_one.jsonl").write_text(json.dumps({"kind":"EVENT","event":"HANDSHAKE_SUCCESS","token":"secret"})+"\n")
            a = audit(Path(td))
        self.assertEqual(a["gate"], "HANDSHAKE_ONLY_NO_ORDERBOOK_DATA_EVIDENCE")
        self.assertEqual(a["handshake_events"], 1)

    def test_data_rows_are_not_exec_proof(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "data"; d.mkdir()
            (d / "finam_ws_two.jsonl").write_text(json.dumps({"kind":"DATA","subscription_type":"ORDER_BOOK","captured_utc":"2026-10-16T11:50:00Z","payload":{"order_book":[{"symbol":"CRZ6CRH7@RTSX","rows":[{"price":"100.0","buy_size":"8","sell_size":"0","timestamp":"2026-10-16T11:49:59Z"},{"price":"101.0","buy_size":"0","sell_size":"8","timestamp":"2026-10-16T11:49:58Z"}]}]},"secret_key":"tapi_sk_sensitive"})+"\n",encoding="utf8")
            a = audit(Path(td))
        self.assertEqual(a["gate"], "MARKET_DATA_RECORDED_NEEDS_REPLAY_AND_COST_GATE")
        self.assertEqual(a["market_data_envelopes"], 1)
        self.assertEqual(a["market_rows"], 2)
        self.assertEqual(a["market_symbols"], {"CRZ6CRH7@RTSX":1})
        self.assertNotIn("tapi_sk",json.dumps(a))
        self.assertIsNone(a.get("pnl"))

    def test_raw_sensitive_event_and_log_never_printed(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); (base/'data').mkdir(); (base/'logs').mkdir()
            (base/'status.json').write_text(json.dumps({"state":"CONNECTING","cycles":6,"secret":"tapi_sk_secret"}))
            (base/'data'/'finam_ws_test.jsonl').write_text('\n'.join([
                json.dumps({"kind":"EVENT","event":"SUBSCRIBED","token":"tapi_sk_secret"}),
                json.dumps({"kind":"ERROR","error_code":12,"error_message":"private-account-token"}),
                json.dumps({"kind":"DATA","subscription_type":"ACCOUNT","account_id":"private-account"})])+'\n')
            (base/'logs'/'collector.stdout.log').write_text('\n'.join([json.dumps({"cycle":1,"status":"SUBSCRIPTIONS_SENT_NO_MARKET_DATA","market_messages":0,"jwt":"sensitive"}),json.dumps({"cycle":2,"status":"DATA_RECORDED","market_messages":4})]))
            a=audit(base)
        self.assertEqual(a["subscription_ack_events"],1)
        self.assertEqual(a["error_envelopes"],1)
        self.assertEqual(a["market_data_envelopes"],0)
        self.assertEqual(a["log_reported_market_messages"],4)
        self.assertEqual(a["log_empty_rounds"],1)
        self.assertEqual(a["gate"],"LOG_REPORTS_DATA_BUT_FILES_NOT_VERIFIED")
        self.assertNotIn('private-account',json.dumps(a))
        self.assertNotIn('sensitive',json.dumps(a))

    def test_partial_file_does_not_fabricate_data(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td); (base/'data').mkdir()
            (base/'data'/'finam_ws_part.jsonl').write_text('{broken frame\n',encoding='utf8')
            a=audit(base)
        self.assertEqual(a["files_unreadable_or_partial"],1)
        self.assertEqual(a["market_data_envelopes"],0)

    def test_ignores_state_values_that_could_hide_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);base.mkdir(exist_ok=True)
            (base/'status.json').write_text(json.dumps({"state":"READONLY_SECRET_tapi_sk_abc", "cycles":"secret"}))
            a=audit(base)
        self.assertEqual(a["current_state"],"UNKNOWN")
        self.assertIsNone(a["cycles_reported"])
        self.assertNotIn("tapi_sk_abc",json.dumps(a))

    def test_oversize_bound(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);(base/'data').mkdir()
            with (base/'data'/'finam_ws_big.jsonl').open('wb') as f:
                f.truncate(4_000_001)
            a=audit(base)
        self.assertEqual(a["files_oversize_or_skipped"],1)
        self.assertEqual(a["market_data_envelopes"],0)

if __name__=='__main__': unittest.main()