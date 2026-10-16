"""Synthetic read-only parity tests, no broker requests, credentials or trading."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import unittest

from spread_leg_parity import MarketQuote,evaluate_spread_vs_futures
from triangle_gate import ConfirmedCosts

NOW=datetime(2026,10,16,9,0,0,tzinfo=timezone.utc)
COST=ConfirmedCosts(Decimal("0.1"),Decimal("0.1"),Decimal("0.1"),Decimal("0.1"),True)


def quote(symbol,kind,bid,ask,bq="10",aq="10",**kw):
    return replace(MarketQuote(symbol=symbol,instrument_kind=kind,bid=Decimal(bid),ask=Decimal(ask),
        bid_qty=Decimal(bq),ask_qty=Decimal(aq),received_at=NOW,account_tradable=True,
        can_buy=True,can_sell=True,rub_per_point=Decimal("2"),one_order_cost=COST),**kw)


def normal():
    return (
        quote("ABSPREAD@RTSX","SPREAD","8","9",bq="3"),
        quote("FARAB@RTSX","FUTURE","100","101"),
        quote("FARBC@RTSX","FUTURE","115","116",bq="4"),
    )


def evaluate(*books,**kwargs):
    return evaluate_spread_vs_futures(*(books or normal()),as_of=NOW,
        signed_orientation_verified=True,equal_contract_lots_verified=True,**kwargs)


class SpreadLegParityTests(unittest.TestCase):
    def test_buy_native_sell_synthetic_positive_with_three_orders(self):
        x=evaluate()
        self.assertEqual(len(x["positive_directions"]),1)
        p=x["positive_directions"][0]
        self.assertEqual(p["direction"],"BUY_NATIVE_SELL_SYNTHETIC")
        self.assertEqual(p["quantity_contracts"],4)
        # Gross: FAR.bid 115 - NEAR.ask 101 - SPREAD.ask 9 = 5pts *2Rub*4 =40;
        # Cost: 3 orders *0.4Rub *4 =4.8 => 35.2RUB hypothetical.
        self.assertEqual(p["modeled_net_rub"],"35.2")
        self.assertEqual(x["broker_orders"],0)
        self.assertIsNone(x["realized_pnl_rub"])
        self.assertFalse(x["execution_proven"])

    def test_reverse_native_sell_synthetic_buy_direction(self):
        s,n,f=normal()
        s=replace(s,bid=Decimal("18"),ask=Decimal("19"))
        f=replace(f,bid=Decimal("110"),ask=Decimal("111"))
        x=evaluate(s,n,f)
        self.assertEqual(len(x["positive_directions"]),1)
        self.assertEqual(x["positive_directions"][0]["direction"],"SELL_NATIVE_BUY_SYNTHETIC")

    def test_unknown_orientation_blocks(self):
        s,n,f=normal()
        x=evaluate_spread_vs_futures(s,n,f,as_of=NOW,equal_contract_lots_verified=True)
        self.assertEqual(x["rejections"],["SYNTHETIC_IDENTITY_NOT_BROKER_VERIFIED"])

    def test_unknown_contract_ratio_blocks(self):
        s,n,f=normal()
        x=evaluate_spread_vs_futures(s,n,f,as_of=NOW,signed_orientation_verified=True)
        self.assertEqual(x["positive_directions"],[])

    def test_fee_unverified_blocks(self):
        s,n,f=normal()
        x=evaluate(s,n,replace(f,one_order_cost=None))
        self.assertEqual(x["rejections"],["ACCOUNT_TARIFF_UNVERIFIED"])

    def test_market_quote_from_future_arriving_late_blocks(self):
        s,n,f=normal()
        x=evaluate(s,n,replace(f,received_at=NOW-timedelta(milliseconds=300)))
        self.assertEqual(x["rejections"],["NOT_SIMULTANEOUS_OR_NOT_CURRENT"])

    def test_future_ahead_of_as_of_blocks(self):
        s,n,f=normal()
        x=evaluate(s,n,replace(f,received_at=NOW+timedelta(milliseconds=1)))
        self.assertEqual(x["positive_directions"],[])

    def test_mixed_monetary_value_not_assumed_equal(self):
        s,n,f=normal()
        x=evaluate(s,n,replace(f,rub_per_point=Decimal("10")))
        self.assertEqual(x["rejections"],["MONETARY_UNIT_MISMATCH"])

    def test_account_access_blocks(self):
        s,n,f=normal()
        x=evaluate(s,replace(n,account_tradable=False),f)
        self.assertEqual(x["rejections"],["ACCOUNT_NOT_ELIGIBLE"])

    def test_quantity_blocks_one_path(self):
        s,n,f=normal()
        x=evaluate(s,n,replace(f,bid_qty=Decimal("0.5")))
        self.assertEqual(len(x["positive_directions"]),0)
        self.assertIn("INSUFFICIENT_THREE_BOOK_DEPTH_BUY_NATIVE_SELL_SYNTHETIC",x["rejections"])

    def test_negative_native_spread_price_is_valid(self):
        s,n,f=normal()
        s=replace(s,bid=Decimal("-3"),ask=Decimal("-2"))
        x=evaluate(s,n,f)
        self.assertGreaterEqual(len(x["positive_directions"]),1)

    def test_unverified_tariff_never_default_free(self):
        s,n,f=normal()
        x=evaluate(replace(s,one_order_cost=replace(COST,tariff_verified=False)),n,f)
        self.assertEqual(x["positive_directions"],[])

    def test_incompatible_instruments_throw(self):
        s,n,f=normal()
        with self.assertRaisesRegex(ValueError,"INCOMPATIBLE_THREE_INSTRUMENTS"):
            evaluate(replace(s,instrument_kind="FUTURE"),n,f)

    def test_crossed_book_rejected(self):
        s,n,f=normal()
        with self.assertRaisesRegex(ValueError,"CROSSED_OR_LOCKED_BOOK"):
            evaluate(s,replace(n,bid=Decimal("110")),f)

    def test_default_no_proof_is_disabled(self):
        s,n,f=normal()
        x=evaluate_spread_vs_futures(s,n,f,as_of=NOW)
        self.assertFalse(x["execution_proven"])
        self.assertEqual(x["positive_directions"],[])

    def test_unprofitable_after_fees_rejected(self):
        s,n,f=normal()
        f=replace(f,bid=Decimal("110"),ask=Decimal("111"))
        x=evaluate(s,n,f)
        self.assertEqual(len(x["positive_directions"]),0)
        self.assertTrue(any(k.startswith("NONPOSITIVE") for k in x["rejections"]))

if __name__=="__main__":unittest.main()