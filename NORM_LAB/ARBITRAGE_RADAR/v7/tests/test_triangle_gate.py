"""Synthetic offline arithmetic and fail-closed cases. No broker keys or traffic."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from triangle_gate import ConfirmedCosts, SpreadQuote, scan_triangles

NOW = datetime(2026, 10, 16, 9, 0, 0, tzinfo=timezone.utc)
COST = ConfirmedCosts(Decimal("0.1"), Decimal("0.1"), Decimal("0.1"), Decimal("0.1"), True)


def quote(sym, a, b, bid, ask, bid_qty="10", ask_qty="10", **overrides):
    obj = SpreadQuote(
        symbol=sym, underlying="X", near_contract=a, far_contract=b,
        bid=Decimal(bid), ask=Decimal(ask), bid_qty=Decimal(bid_qty), ask_qty=Decimal(ask_qty),
        received_at=NOW, rub_per_price_unit=Decimal("2"), costs=COST,
        orientation="FAR_MINUS_NEAR", contract_identity_verified=True,
        account_tradable=True, can_buy=True, can_sell=True, lot_size=Decimal("1"),
    )
    return replace(obj, **overrides)


def triangle():
    return [quote("XAB@RTSX", "A", "B", "9", "10"),
            quote("XBC@RTSX", "B", "C", "19", "20"),
            quote("XAC@RTSX", "A", "C", "33", "34", bid_qty="3")]


class TriangleGateTests(unittest.TestCase):
    def test_direct_overprice_after_three_full_cost_sets_and_depth(self):
        report = scan_triangles(triangle(), as_of=NOW)
        self.assertEqual(report["complete_triangles"], 1)
        self.assertEqual(report["positive_model_directions"], 1)
        self.assertEqual(report["broker_orders"], 0)
        self.assertFalse(report["execution_proven"])
        c = report["candidates"][0]
        self.assertEqual(c["direction"], "SELL_DIRECT_BUY_COMPONENTS")
        self.assertEqual(c["contracts"], 3)
        # 33 - 10 - 20 = 3 price points -> 6 RUB per unit gross;
        # three orders * 0.4 RUB * three units = 3.6 RUB costs, 14.4 net.
        self.assertEqual(c["modeled_net_rub"], "14.4")
        self.assertEqual(c["status"], "MODEL_ONLY_ATOMIC_EXECUTION_UNPROVEN")

    def test_reverse_price_direction(self):
        q = [quote("XAB@RTSX", "A", "B", "12", "13"),
             quote("XBC@RTSX", "B", "C", "22", "23"),
             quote("XAC@RTSX", "A", "C", "30", "31")]
        report = scan_triangles(q, as_of=NOW)
        self.assertEqual(report["positive_model_directions"], 1)
        self.assertEqual(report["candidates"][0]["direction"], "BUY_DIRECT_SELL_COMPONENTS")

    def test_two_consecutive_spreads_without_third_cannot_claim_profit(self):
        report = scan_triangles(triangle()[:2], as_of=NOW)
        self.assertEqual(report["sequential_chains"], 1)
        self.assertEqual(report["complete_triangles"], 0)
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["rejections"]["MISSING_DIRECT_SPREAD"], 1)

    def test_negative_calendar_quotes_do_not_break_arithmetic(self):
        q = [quote("XAB@RTSX", "A", "B", "-3", "-2"),
             quote("XBC@RTSX", "B", "C", "-5", "-4"),
             quote("XAC@RTSX", "A", "C", "-5", "-4.9")]
        self.assertEqual(scan_triangles(q, as_of=NOW)["positive_model_directions"], 1)

    def test_missing_account_tariff_is_not_assumed_zero(self):
        q = triangle()
        q[0] = replace(q[0], costs=None)
        r = scan_triangles(q, as_of=NOW)
        self.assertEqual(r["positive_model_directions"], 0)
        self.assertEqual(r["rejections"]["ACCOUNT_TARIFF_UNVERIFIED"], 1)

    def test_unconfirmed_fee_flag_blocks(self):
        q=triangle()
        q[1]=replace(q[1],costs=replace(COST,tariff_verified=False))
        self.assertEqual(scan_triangles(q, as_of=NOW)["positive_model_directions"],0)

    def test_wrong_signed_contract_orientation_blocks(self):
        q=triangle()
        q[1]=replace(q[1],orientation="NEAR_MINUS_FAR")
        self.assertEqual(scan_triangles(q,as_of=NOW)["rejections"]["ALGEBRA_NOT_VERIFIED"],1)

    def test_unconfirmed_account_rights_block(self):
        q=triangle()
        q[0]=replace(q[0],account_tradable=False)
        self.assertEqual(scan_triangles(q,as_of=NOW)["positive_model_directions"],0)

    def test_no_sell_right_blocks_only_one_direction(self):
        q=triangle()
        q[2]=replace(q[2],can_sell=False)
        r=scan_triangles(q,as_of=NOW)
        self.assertEqual(r["positive_model_directions"],0)
        self.assertGreaterEqual(r["rejections"]["TRADE_DIRECTION_NOT_ALLOWED"],1)

    def test_missing_lot_multipliers_blocks(self):
        q=triangle()
        q[2]=replace(q[2],rub_per_price_unit=Decimal("4"))
        self.assertEqual(scan_triangles(q,as_of=NOW)["rejections"]["CONTRACT_MULTIPLIER_OR_LOT_UNVERIFIED"],1)

    def test_stale_and_future_snapshot_blocks(self):
        q=triangle()
        q[1]=replace(q[1],received_at=NOW-timedelta(seconds=2))
        self.assertEqual(scan_triangles(q,as_of=NOW)["rejections"]["NOT_SIMULTANEOUS_OR_NOT_CURRENT"],1)
        q[1]=replace(q[1],received_at=NOW+timedelta(milliseconds=1))
        self.assertEqual(scan_triangles(q,as_of=NOW)["positive_model_directions"],0)

    def test_crossed_book_rejected_not_arbitrage(self):
        q=triangle()
        q[0]=replace(q[0],bid=Decimal("11"))
        with self.assertRaisesRegex(ValueError,"CROSSED_OR_LOCKED_BOOK"):
            scan_triangles(q,as_of=NOW)

    def test_reverse_fee_guard_eliminates_tiny_edge(self):
        q=[quote("XAB@RTSX", "A", "B", "9", "10"),
           quote("XBC@RTSX", "B", "C", "19", "20"),
           quote("XAC@RTSX", "A", "C", "30.05", "31")]
        r=scan_triangles(q,as_of=NOW)
        self.assertEqual(r["positive_model_directions"],0)
        self.assertGreater(r["rejections"].get("NONPOSITIVE_AFTER_ALL_DECLARED_COSTS",0),0)

    def test_unrelated_underlying_never_synthesized(self):
        q=triangle()
        q[2]=replace(q[2],underlying="Y")
        r=scan_triangles(q,as_of=NOW)
        self.assertEqual(r["complete_triangles"],0)

    def test_fractional_size_rounds_down_not_up(self):
        q=triangle()
        q[2]=replace(q[2],bid_qty=Decimal("0.9"))
        r=scan_triangles(q,as_of=NOW)
        self.assertEqual(r["positive_model_directions"],0)
        self.assertGreater(r["rejections"].get("INSUFFICIENT_THREE_LEG_LIQUIDITY",0),0)

    def test_maximum_result_cap_and_no_real_pnl(self):
        r=scan_triangles(triangle(),as_of=NOW,max_results=1)
        self.assertEqual(r["realized_pnl_rub"],None)
        self.assertEqual(r["status"],"OFFLINE_MODEL_ONLY_NOT_AN_EXECUTION_SIGNAL")

    def test_duplicate_contract_pair_fails_closed(self):
        q=triangle()
        with self.assertRaisesRegex(ValueError,"DUPLICATE_CONTRACT_PAIR"):
            scan_triangles(q+[q[0]],as_of=NOW)


if __name__ == "__main__":
    unittest.main()