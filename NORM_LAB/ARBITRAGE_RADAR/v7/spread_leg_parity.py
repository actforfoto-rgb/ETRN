"""Offline three-book native-calendar vs two outright-futures parity gate.

MOEX supports synthetic matching between a calendar spread and its two
underlying futures order books. This model deliberately makes NO assumption
that such a fleeting discrepancy is executable after fees or fills.

All prices must have independently VERIFIED common additive direction and
monetary point value, with lot 1:1:1. Never infer from ticker text alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
import re

from triangle_gate import ConfirmedCosts, dec, utc

_SYMBOL = re.compile(r"^[A-Z0-9_.-]{2,48}@RTSX$")


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    instrument_kind: str  # "SPREAD" or "FUTURE"
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    received_at: datetime
    account_tradable: bool = False
    can_buy: bool = False
    can_sell: bool = False
    rub_per_point: Decimal | None = None
    one_order_cost: ConfirmedCosts | None = None

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not _SYMBOL.fullmatch(self.symbol):
            raise ValueError("INVALID_SYMBOL")
        if self.instrument_kind not in ("SPREAD", "FUTURE"):
            raise ValueError("INVALID_INSTRUMENT_KIND")
        if dec(self.bid) >= dec(self.ask):
            raise ValueError("CROSSED_OR_LOCKED_BOOK")
        dec(self.bid_qty, nonnegative=True)
        dec(self.ask_qty, nonnegative=True)
        utc(self.received_at)
        if self.rub_per_point is not None:
            dec(self.rub_per_point, positive=True)
        if self.one_order_cost is not None:
            self.one_order_cost.per_order()


def evaluate_spread_vs_futures(
    spread: MarketQuote, near: MarketQuote, far: MarketQuote, *,
    as_of: datetime, signed_orientation_verified: bool = False,
    equal_contract_lots_verified: bool = False,
    max_age_ms: int = 1000, max_skew_ms: int = 250,
    max_contracts: int = 10,
) -> dict[str, object]:
    """Search two *simultaneous* parity directions using executable sides.

    Convention if independently confirmed: BUY SPREAD = BUY FAR + SELL NEAR,
    spread price = FAR_PRICE - NEAR_PRICE, one native lot nets 1 lot of each.
    Result is indicative and non-atomic; no order submission is available.
    """
    for q in (spread, near, far):
        q.validate()
    if spread.instrument_kind != "SPREAD" or any(
        x.instrument_kind != "FUTURE" for x in (near, far)
    ) or len({spread.symbol, near.symbol, far.symbol}) != 3:
        raise ValueError("INCOMPATIBLE_THREE_INSTRUMENTS")
    if not (1 <= max_contracts <= 100000 and 1 <= max_age_ms <= 60000 and 1 <= max_skew_ms <= 60000):
        raise ValueError("INVALID_EVALUATION_LIMITS")
    now = utc(as_of)
    observations = [utc(x.received_at) for x in (spread, near, far)]
    skew = int(round((max(observations) - min(observations)).total_seconds()*1000))
    ages = [int(round((now-t).total_seconds()*1000)) for t in observations]

    output: dict[str, object] = {
        "status": "THREE_BOOKS_RESEARCH_ONLY",
        "spread": spread.symbol, "near_future": near.symbol, "far_future": far.symbol,
        "observation_skew_ms": skew,
        "rejections": [], "positive_directions": [],
        "broker_orders": 0, "realized_pnl_rub": None, "execution_proven": False,
        "note": "MOEX synthetic matching can already incorporate futures liquidity; non-atomic model gross/net is not executable or realized profit.",
    }
    rejections: list[str] = output["rejections"]  # type: ignore[assignment]
    if skew > max_skew_ms or any(age<0 or age>max_age_ms for age in ages):
        rejections.append("NOT_SIMULTANEOUS_OR_NOT_CURRENT")
        return output
    if not signed_orientation_verified or not equal_contract_lots_verified:
        rejections.append("SYNTHETIC_IDENTITY_NOT_BROKER_VERIFIED")
        return output
    if any(not x.account_tradable for x in (spread, near, far)):
        rejections.append("ACCOUNT_NOT_ELIGIBLE")
        return output
    if any(x.one_order_cost is None or not x.one_order_cost.tariff_verified for x in (spread, near, far)):
        rejections.append("ACCOUNT_TARIFF_UNVERIFIED")
        return output
    units = [x.rub_per_point for x in (spread, near, far)]
    if any(x is None for x in units) or len({dec(x, positive=True) for x in units}) != 1:
        rejections.append("MONETARY_UNIT_MISMATCH")
        return output
    rub_unit = dec(spread.rub_per_point, positive=True)
    fees = sum((x.one_order_cost.per_order() for x in (spread, near, far)), Decimal("0"))

    # Three separate native executions. Capacity measured on actual trade sides.
    paths = (
        ("BUY_NATIVE_SELL_SYNTHETIC",
         (spread.ask_qty, near.ask_qty, far.bid_qty),
         (spread.can_buy, near.can_buy, far.can_sell),
         dec(far.bid) - dec(near.ask) - dec(spread.ask)),
        ("SELL_NATIVE_BUY_SYNTHETIC",
         (spread.bid_qty, near.bid_qty, far.ask_qty),
         (spread.can_sell, near.can_sell, far.can_buy),
         dec(spread.bid) + dec(near.bid) - dec(far.ask)),
    )
    positives: list[dict[str, object]] = output["positive_directions"]  # type: ignore[assignment]
    for direction, depths, rights, gross_points in paths:
        if not all(rights):
            rejections.append("SIDE_NOT_ALLOWED_"+direction)
            continue
        quantity = int(min(Decimal(max_contracts), *(dec(d,nonnegative=True) for d in depths))
                       .to_integral_value(rounding=ROUND_FLOOR))
        if quantity < 1:
            rejections.append("INSUFFICIENT_THREE_BOOK_DEPTH_"+direction)
            continue
        gross = gross_points * rub_unit * quantity
        costs = fees * quantity
        net = gross - costs
        if net <= 0:
            rejections.append("NONPOSITIVE_AFTER_DECLARED_COSTS_"+direction)
            continue
        positives.append({
            "direction": direction, "quantity_contracts": quantity,
            "gross_price_points_per_contract": str(gross_points),
            "gross_rub": str(gross), "modeled_three_order_cost_rub": str(costs),
            "modeled_net_rub": str(net),
            "status": "MODEL_ONLY_NO_ATOMIC_FILL_OR_LIVE_PROOF",
        })
    positives.sort(key=lambda q:(-Decimal(q["modeled_net_rub"]),q["direction"]))
    return output