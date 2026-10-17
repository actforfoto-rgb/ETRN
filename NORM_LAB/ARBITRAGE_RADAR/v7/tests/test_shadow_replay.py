"""Deterministic tests for causal shadow replay; ALL market events are synthetic.

No network, no broker keys, no real fills, no LIVE or model PnL promotion.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from hashlib import sha256
import json
import unittest

from shadow_replay import (
    ShadowConfig, ShadowError, read_sanitized_tape, replay_file,
    replay_sanitized_events, dec,
)

SYM = "W4X6W4Z6@RTSX"
T = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def stamp(s: float) -> str:
    return (T + timedelta(seconds=s)).isoformat()


def config(**kwargs) -> ShadowConfig:
    spec = dict(symbol=SYM, trading_session="2026-10-19:RTSX",
                rub_per_spread_point=Decimal("1"), min_width_points=Decimal("5"),
                lots=Decimal("1"), latency_ms=250,
                public_trade_units_verified=True,
                aggressor_side_semantics_verified=True,
                actual_account_costs_verified=True,
                native_broker_order_route_verified=True,
                capital_rub=Decimal("10000"), margin_rub=Decimal("3000"),
                reserve_rub=Decimal("2000"),
                broker_roundtrip_rub=Decimal("2"),
                maker_entry_exchange_fee_rub=Decimal("0"),
                maker_exit_exchange_fee_rub=Decimal("0"),
                taker_exit_exchange_fee_rub=Decimal("3"))
    spec.update(kwargs)
    return ShadowConfig(**spec)


def book_event(s: float, *, bid="100", ask="110", bid_qty="1",
               ask_qty="1", symbol=SYM, snapshot=True, delta=False):
    rows = [{"price": bid, "buy_size": bid_qty, "timestamp": stamp(s)},
            {"price": ask, "sell_size": ask_qty, "timestamp": stamp(s)}]
    if delta:
        for row in rows:
            row["action"] = "ACTION_UPDATE"
    return {"kind": "DATA", "schema": "v7_test_synthetic", "captured_utc": stamp(s),
            "subscription_type": "ORDER_BOOK", "provider_timestamp": None,
            "payload": {"order_book": [{
                "symbol": symbol, "rows": rows,
                "is_data_snapshot": snapshot}]}}


def print_event(s: float, *, price="99", size="2", side="SIDE_SELL",
                tid="a"*24, symbol=SYM, print_at=None, items=None):
    if items is None:
        items = [{"price": price, "size": size,
                  "timestamp": stamp(s if print_at is None else print_at),
                  "side": side, "public_trade_id_hash": tid}]
    return {"kind": "TRADE_PRINTS", "schema": "v7_test_synthetic",
            "captured_utc": stamp(s), "subscription_type": "INSTRUMENT_TRADES",
            "payload": {"symbol": symbol, "trades": items}}


def normal_cycle():
    return [
        book_event(0),
        print_event(1, price="99", size="2", side="SIDE_SELL", tid="a"*24),
        book_event(2, snapshot=False, delta=True),
        print_event(3, price="111", size="2", side="SIDE_BUY", tid="b"*24),
    ]


def run(events, **options):
    return replay_sanitized_events(events, config(**options), trusted_receipt=True,
        collector_metrics={"errors": 0, "missing_or_unrecognized_data_frames": 0,
                           "limits_reached": False})


class ReplayCausality(unittest.TestCase):
    def test_two_separate_trade_through_events_create_only_conditional_cycle(self):
        r = run(normal_cycle())
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 1)
        x = r["conditional_cycle_rows"][0]
        self.assertEqual(x["gross_rub_proxy"], "10")
        self.assertEqual(x["net_rub_proxy_if_costs_verified"], "8")
        self.assertNotEqual(x["entry_public_print_ids"], x["exit_public_print_ids"])
        self.assertFalse(x["broker_fills_proven"])
        self.assertFalse(r["profitability_proven"])
        self.assertIsNone(r["realized_pnl_rub"])
        self.assertEqual(r["actual_orders"], 0)

    def test_real_market_profit_never_claimed_even_when_assumptions_verification_true(self):
        r = run(normal_cycle())
        self.assertEqual(r["actual_broker_fills"], 0)
        self.assertEqual(r["status"], "CONDITIONAL_COMPLETED_CYCLES_UNVERIFIED_FILLS")
        self.assertIn("EXCHANGE_ORDERBOOK_SEQUENCE_NOT_VERIFIABLE",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_zero_trades_can_never_fill_order(self):
        r = run([book_event(0), book_event(5)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertIsNone(r["conditional_net_rub_if_fees_verified"])
        self.assertEqual(r["hypothetical_orders_posted"], 1)

    def test_two_snapshots_nlmk_price_move_is_not_trade(self):
        a = book_event(0, bid="207", ask="230")
        b = book_event(11, bid="228", ask="231", snapshot=False, delta=True)
        r = run([a, b], min_width_points=Decimal("1"))
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertGreaterEqual(r["hypothetical_orders_cancelled"], 1)
        self.assertIsNone(r["conditional_net_rub_if_fees_verified"])

    def test_trade_touch_does_not_fill(self):
        r = run([book_event(0), print_event(1, price="100"), book_event(2),
                 print_event(3, price="110", side="SIDE_BUY", tid="b"*24)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_wrong_taker_direction_does_not_fill(self):
        r = run([book_event(0), print_event(1, side="SIDE_BUY")])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_trade_from_wrong_instrument_does_not_fill(self):
        r = run([book_event(0), print_event(1, symbol="NMZ6NMH7@RTSX")])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_wrong_side_semantics_unverified_blocks_even_proxy(self):
        r = run(normal_cycle(), aggressor_side_semantics_verified=False)
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertIn("TRADE_QUANTITY_OR_AGGRESSOR_SEMANTICS_NOT_VERIFIED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_trade_size_unit_unverified_blocks_even_proxy(self):
        r = run(normal_cycle(), public_trade_units_verified=False)
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_trade_before_latency_is_rejected(self):
        r = run([book_event(0), print_event(.1)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_delayed_old_print_is_rejected(self):
        r = run([book_event(0), print_event(8, print_at=1)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_market_print_before_order_not_replayed_from_future(self):
        r = run([print_event(0), book_event(1), print_event(2, size="1")])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_queue_ahead_consumes_volume_without_fill(self):
        r = run([book_event(0, bid_qty="5"), print_event(1, size="2"),
                 print_event(2, size="3", tid="b"*24)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertEqual(r["open_conditional_lots"], "0")

    def test_two_prints_after_queue_ahead_can_fill_entry(self):
        r = run([book_event(0, bid_qty="1"), print_event(1, size="1"),
                 print_event(2, size="1", tid="b"*24)])
        self.assertEqual(r["open_conditional_lots"], "1")
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_partial_entry_is_open_risk_and_not_zero(self):
        r = run([book_event(0, bid_qty="1"),
                 print_event(1, size="1.5")])
        self.assertEqual(r["open_conditional_lots"], "0.5")
        self.assertTrue(r["unresolved_exposure"])
        self.assertEqual(r["status"], "OPEN_OR_PARTIALLY_FILLED_RISK_NOT_CLOSED")

    def test_partial_exit_is_open_risk(self):
        r = run([book_event(0), print_event(1),
                 book_event(2, snapshot=False, delta=True),
                 print_event(3, price="111", size="1.5", side="SIDE_BUY",
                             tid="b"*24)])
        self.assertEqual(r["open_conditional_lots"], "0.5")
        self.assertTrue(r["unresolved_exposure"])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_identical_print_id_cannot_fill_both_entry_and_exit(self):
        r = run([book_event(0), print_event(1),
                 book_event(2, snapshot=False, delta=True), print_event(3, price="111", side="SIDE_BUY",
                             tid="a"*24)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertIn("CONFLICTING_EXCHANGE_TRADE_ID",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_repeated_same_print_id_is_deduplicated(self):
        e = print_event(1, size="1")
        r = run([book_event(0), e, e])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertEqual(r["unique_public_trade_ids"], 1)

    def test_missing_exchange_trade_id_is_not_usable(self):
        r = run([book_event(0), print_event(1, tid="")])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_out_of_order_captured_time_invalid(self):
        r = run([book_event(3), print_event(1)])
        self.assertIn("ARRIVAL_TIME_REVERSED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_provider_error_invalidates_open_position(self):
        r = run([book_event(0), print_event(1),
                 {"kind":"ERROR", "captured_utc":stamp(2),"error_code":503}])
        self.assertTrue(r["unresolved_exposure"])
        self.assertIn("PROVIDER_STREAM_INTERRUPTED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_max_holding_force_exit_uses_bbo_only_not_second_trade(self):
        r = run([book_event(0), print_event(1),
                 book_event(5, snapshot=False, delta=True)], max_position_hold_ms=2000)
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)
        self.assertEqual(len(r["forced_exit_bbo_only_scenarios"]), 1)
        self.assertEqual(r["forced_exit_bbo_only_scenarios"][0]["classification"],
                         "FORCED_TAKER_BBO_ONLY_NO_PRINT")
        self.assertTrue(r["unresolved_exposure"])
        self.assertEqual(r["open_conditional_lots"], "1")
        self.assertFalse(r["profitability_proven"])

    def test_missing_costs_yields_no_net_even_if_two_proxy_fills(self):
        r = run(normal_cycle(), actual_account_costs_verified=False)
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 1)
        self.assertIsNone(r["conditional_net_rub_if_fees_verified"])
        self.assertIn("ACTUAL_ACCOUNT_COMMISSION_UNKNOWN",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_margin_too_high_blocks_entry(self):
        r = run(normal_cycle(), margin_rub=Decimal("12000"))
        self.assertEqual(r["capital_gate"], "INSUFFICIENT_CAPITAL")
        self.assertEqual(r["hypothetical_orders_posted"], 0)

    def test_no_snapshot_delta_does_not_create_trade(self):
        r = run([book_event(0, snapshot=False, delta=True),
                 print_event(1)])
        self.assertEqual(r["hypothetical_orders_posted"], 0)

    def test_no_market_data_does_not_claim_money(self):
        r = run([])
        self.assertIn("EMPTY_MARKET_TAPE", r["invalid_or_unverified_evidence_reasons"])
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 0)

    def test_negative_spread_price_is_valid_arithmetic(self):
        r = run([book_event(0, bid="-40", ask="-20"),
                 print_event(1, price="-41"),
                 book_event(2, bid="-40", ask="-20", snapshot=False, delta=True),
                 print_event(3, price="-19", side="SIDE_BUY", tid="b"*24)],
                min_width_points=Decimal("5"))
        self.assertEqual(r["conditionally_closed_two_print_cycles"], 1)
        self.assertEqual(r["conditional_cycle_rows"][0]["gross_rub_proxy"], "20")

    def test_unknown_collector_frame_loss_blocks_quality_claim(self):
        r = replay_sanitized_events(normal_cycle(), config(), trusted_receipt=True,
           collector_metrics={"errors":0, "limits_reached":False,
                              "missing_or_unrecognized_data_frames":2})
        self.assertIn("COLLECTOR_LOSS_OR_TRUNCATION",
                      r["invalid_or_unverified_evidence_reasons"])
        self.assertFalse(r["profitability_proven"])

    def test_untrusted_receipt_is_reported(self):
        r = replay_sanitized_events(normal_cycle(), config(), trusted_receipt=False,
           collector_metrics={"errors":0, "limits_reached":False,
                              "missing_or_unrecognized_data_frames":0})
        self.assertIn("SHA256_NOT_EXTERNALLY_ATTESTED",
                      r["invalid_or_unverified_evidence_reasons"])


    def test_forced_bbo_quote_does_not_release_inventory_or_enable_reentry(self):
        r = run([book_event(0), print_event(1),
                 book_event(5, snapshot=False, delta=True),
                 book_event(6, snapshot=False, delta=True)],
                max_position_hold_ms=2000)
        self.assertEqual(len(r["forced_exit_bbo_only_scenarios"]),1)
        self.assertEqual(r["conditionally_closed_two_print_cycles"],0)
        self.assertEqual(r["hypothetical_orders_posted"],1)
        self.assertEqual(r["open_conditional_lots"],"1")
        self.assertIn("FORCED_TAKER_BBO_ONLY_NO_EXECUTION_OPEN_RISK",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_order_reprice_cancels_without_ack_and_does_not_create_new_order(self):
        r = run([book_event(0),
                 book_event(2, bid="101", ask="110", snapshot=False, delta=True),
                 book_event(3, bid="102", ask="110", snapshot=False, delta=True)])
        self.assertEqual(r["hypothetical_orders_posted"],1)
        self.assertEqual(r["hypothetical_orders_cancelled"],1)
        self.assertIn("REPRICE_ORDER_CANCEL_NOT_ACKNOWLEDGED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_second_snapshot_with_live_order_blocks_session_proof(self):
        r = run([book_event(0), print_event(1), book_event(2)])
        self.assertEqual(r["conditionally_closed_two_print_cycles"],0)
        self.assertTrue(r["unresolved_exposure"])
        self.assertIn("BOOK_RESNAPSHOT_SESSION_CONTINUITY_UNVERIFIED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_quote_degrades_while_order_resting_stop_not_reenter(self):
        old = book_event(3, snapshot=False, delta=True)
        for row in old["payload"]["order_book"][0]["rows"]:
            row["timestamp"] = stamp(0)
        r = run([book_event(0),old,book_event(4, snapshot=False, delta=True)])
        self.assertEqual(r["hypothetical_orders_posted"],1)
        self.assertIn("BOOK_DEGRADED_CANCEL_NOT_ACKNOWLEDGED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_timed_out_pending_order_does_not_restart_without_cancel_ack(self):
        r = run([book_event(0),book_event(3, snapshot=False, delta=True)],
                max_order_wait_ms=1500)
        self.assertEqual(r["hypothetical_orders_posted"],1)
        self.assertIn("TIMED_OUT_ORDER_CANCEL_NOT_ACKNOWLEDGED",
                      r["invalid_or_unverified_evidence_reasons"])

    def test_exit_reprice_after_entry_leaves_position_open(self):
        r = run([book_event(0),print_event(1),
                 book_event(2, snapshot=False, delta=True),
                 book_event(3, bid="100",ask="112",snapshot=False,delta=True)])
        self.assertTrue(r["unresolved_exposure"])
        self.assertEqual(r["conditionally_closed_two_print_cycles"],0)
        self.assertIn("REPRICE_ORDER_CANCEL_NOT_ACKNOWLEDGED",
                      r["invalid_or_unverified_evidence_reasons"])



class InputAndSafety(unittest.TestCase):
    def test_source_sha_match_and_mismatch(self):
        payload = "".join(json.dumps(row,ensure_ascii=False)+"\n" for row in normal_cycle()).encode()
        with TemporaryDirectory() as folder:
            f = Path(folder)/"safe.jsonl"
            f.write_bytes(payload)
            expected = sha256(payload).hexdigest()
            loaded, prov = read_sanitized_tape(f, expected_sha256=expected)
            self.assertEqual(len(loaded), 4)
            self.assertTrue(prov["hash_matched_external_receipt"])
            self.assertEqual(replay_file(f,config(),expected_sha256=expected)["input_provenance"]["sha256"],expected)
            with self.assertRaisesRegex(ShadowError,"TAPE_HASH_MISMATCH"):
                read_sanitized_tape(f, expected_sha256="a"*64)

    def test_unsanitized_private_keys_rejected(self):
        with TemporaryDirectory() as folder:
            f = Path(folder)/"private.jsonl"
            f.write_text(json.dumps({"kind":"DATA", "captured_utc":stamp(1),
                 "account_id":"PRIVATE", "token":"PRIVATE"})+"\n")
            with self.assertRaises(ShadowError):
                read_sanitized_tape(f)

    def test_no_file_network_or_credentials_ever_needed(self):
        r=run(normal_cycle())
        self.assertEqual(r["actual_orders"],0)
        self.assertIsNone(r["realized_pnl_rub"])

    def test_config_rejects_ambiguous_time_and_bad_lots(self):
        with self.assertRaises(ShadowError):
            config(trading_session="2026-10-19").validate()
        with self.assertRaises(ShadowError):
            config(lots=Decimal(".5")).validate()
        with self.assertRaises(ShadowError):
            config(rub_per_spread_point=Decimal("NaN")).validate()

    def test_numeric_nan_and_float_rejected(self):
        for x in (float("nan"),float("inf"),True):
            with self.assertRaises(ShadowError):
                dec(x)


if __name__ == "__main__":
    unittest.main()
