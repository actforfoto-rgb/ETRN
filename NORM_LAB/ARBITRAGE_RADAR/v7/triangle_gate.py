"""Conservative OFFLINE gate for same-underlying native calendar-spread triangles.

A--B + B--C = A--C only after the broker's exact signed contract identity,
orientation, lot, monetary multiplier and tariffs have been verified.
This module never claims order fillability, risk-free profit or account PnL.
It performs no network, key handling or trading actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import re
from typing import Iterable

MAX_INSTRUMENTS = 500
_MAX_CANDIDATES = 100
_SYMBOL = re.compile(r"^[A-Z0-9_.-]{2,48}@RTSX$")


def dec(value: object, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_AMOUNT")
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("INVALID_AMOUNT") from None
    if not n.is_finite() or (positive and n <= 0) or (nonnegative and n < 0):
        raise ValueError("INVALID_AMOUNT")
    return n


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("UNVERIFIED_CLOCK")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ConfirmedCosts:
    """One *single native spread order*, in RUB/contract, for either direction.

    No zero-default or market-price-based fee inference is permitted. In real
    integration broker/exchange tariffs and buffers must be sourced explicitly.
    """
    broker_rub: Decimal
    exchange_rub: Decimal
    slippage_reserve_rub: Decimal
    execution_failure_reserve_rub: Decimal
    tariff_verified: bool

    def per_order(self) -> Decimal:
        parts = (self.broker_rub, self.exchange_rub,
                 self.slippage_reserve_rub, self.execution_failure_reserve_rub)
        return sum((dec(x, nonnegative=True) for x in parts), Decimal("0"))


@dataclass(frozen=True)
class SpreadQuote:
    symbol: str
    underlying: str
    near_contract: str
    far_contract: str
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    received_at: datetime
    rub_per_price_unit: Decimal | None = None
    costs: ConfirmedCosts | None = None
    orientation: str = "UNVERIFIED"
    contract_identity_verified: bool = False
    account_tradable: bool = False
    can_buy: bool = False
    can_sell: bool = False
    lot_size: Decimal | None = None

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not _SYMBOL.fullmatch(self.symbol):
            raise ValueError("INVALID_RTSX_SYMBOL")
        if any(not isinstance(x, str) or not x.strip() for x in
               (self.underlying, self.near_contract, self.far_contract)):
            raise ValueError("MISSING_INSTRUMENT_IDENTITY")
        if self.near_contract == self.far_contract:
            raise ValueError("SELF_SPREAD_FORBIDDEN")
        if dec(self.bid) >= dec(self.ask):
            raise ValueError("CROSSED_OR_LOCKED_BOOK")
        dec(self.bid_qty, nonnegative=True)
        dec(self.ask_qty, nonnegative=True)
        utc(self.received_at)
        if self.rub_per_price_unit is not None:
            dec(self.rub_per_price_unit, positive=True)
        if self.lot_size is not None:
            dec(self.lot_size, positive=True)
        if self.costs is not None:
            self.costs.per_order()


@dataclass(frozen=True)
class ModelCandidate:
    first: str
    second: str
    direct: str
    direction: str
    contracts: int
    signed_gross_points: Decimal
    gross_rub: Decimal
    modeled_cost_rub: Decimal
    modeled_net_rub: Decimal
    observation_skew_ms: int
    status: str = "MODEL_ONLY_ATOMIC_EXECUTION_UNPROVEN"

    def public_record(self) -> dict[str, object]:
        return {
            "spreads": [self.first, self.second, self.direct],
            "direction": self.direction,
            "contracts": self.contracts,
            "gross_price_units": str(self.signed_gross_points),
            "gross_rub": str(self.gross_rub),
            "modeled_cost_rub": str(self.modeled_cost_rub),
            "modeled_net_rub": str(self.modeled_net_rub),
            "receive_skew_ms": self.observation_skew_ms,
            "status": self.status,
        }


def scan_triangles(quotes: Iterable[SpreadQuote], *, as_of: datetime,
                   max_quote_age_ms: int = 1000, max_skew_ms: int = 250,
                   max_contracts: int = 10, max_results: int = 25) -> dict[str, object]:
    """Check two algebraic directions without using future quotes or filled orders.

    Synthetic tests can prove arithmetic, NOT that broker spread definitions are
    genuinely additive or that three orders can execute atomically.
    """
    now = utc(as_of)
    if not (1 <= max_quote_age_ms <= 60000 and 1 <= max_skew_ms <= 60000):
        raise ValueError("INVALID_TIME_LIMITS")
    if not (1 <= max_contracts <= 100000 and 1 <= max_results <= _MAX_CANDIDATES):
        raise ValueError("INVALID_CAPS")
    items = list(quotes)
    if len(items) > MAX_INSTRUMENTS:
        raise ValueError("UNIVERSE_TOO_LARGE")
    by_pair: dict[tuple[str, str, str], SpreadQuote] = {}
    continuations: dict[tuple[str, str], list[SpreadQuote]] = {}
    for q in items:
        q.validate()
        key = (q.underlying, q.near_contract, q.far_contract)
        if key in by_pair:
            raise ValueError("DUPLICATE_CONTRACT_PAIR")
        by_pair[key] = q
        continuations.setdefault((q.underlying, q.near_contract), []).append(q)

    rejects: dict[str, int] = {}
    def reject(reason: str) -> None:
        rejects[reason] = rejects.get(reason, 0) + 1

    chains = 0
    complete = 0
    positives = 0
    results: list[ModelCandidate] = []
    for ab in items:
        for bc in continuations.get((ab.underlying, ab.far_contract), []):
            if ab.near_contract == bc.far_contract:
                continue
            chains += 1
            ac = by_pair.get((ab.underlying, ab.near_contract, bc.far_contract))
            if ac is None:
                reject("MISSING_DIRECT_SPREAD")
                continue
            complete += 1
            trio = (ab, bc, ac)
            received = [utc(q.received_at) for q in trio]
            skew = round((max(received) - min(received)).total_seconds() * 1000)
            ages = [round((now - t).total_seconds() * 1000) for t in received]
            if skew > max_skew_ms or any(age < 0 or age > max_quote_age_ms for age in ages):
                reject("NOT_SIMULTANEOUS_OR_NOT_CURRENT")
                continue
            if (any(not q.contract_identity_verified or q.orientation != "FAR_MINUS_NEAR" for q in trio)
                    or len({q.underlying for q in trio}) != 1):
                reject("ALGEBRA_NOT_VERIFIED")
                continue
            if any(not q.account_tradable for q in trio):
                reject("ACCOUNT_NOT_ELIGIBLE")
                continue
            if any(q.costs is None or not q.costs.tariff_verified for q in trio):
                reject("ACCOUNT_TARIFF_UNVERIFIED")
                continue
            if (any(q.rub_per_price_unit is None or q.lot_size is None for q in trio)
                    or len({dec(q.rub_per_price_unit) for q in trio}) != 1
                    or len({dec(q.lot_size) for q in trio}) != 1
                    or dec(trio[0].lot_size) != Decimal("1")):
                reject("CONTRACT_MULTIPLIER_OR_LOT_UNVERIFIED")
                continue
            unit = dec(trio[0].rub_per_price_unit, positive=True)
            # Three separate native orders, hence THREE one-way fee sets.
            costs_per_unit = sum((q.costs.per_order() for q in trio), Decimal("0"))
            directions = (
                ("SELL_DIRECT_BUY_COMPONENTS", (ab.ask_qty, bc.ask_qty, ac.bid_qty),
                 (ab.can_buy, bc.can_buy, ac.can_sell),
                 dec(ac.bid) - dec(ab.ask) - dec(bc.ask)),
                ("BUY_DIRECT_SELL_COMPONENTS", (ab.bid_qty, bc.bid_qty, ac.ask_qty),
                 (ab.can_sell, bc.can_sell, ac.can_buy),
                 dec(ab.bid) + dec(bc.bid) - dec(ac.ask)),
            )
            for direction, sizes, permissions, gross_units in directions:
                if not all(permissions):
                    reject("TRADE_DIRECTION_NOT_ALLOWED")
                    continue
                size = min(*(dec(x, nonnegative=True) for x in sizes), Decimal(max_contracts))
                quantity = int(size.to_integral_value(rounding=ROUND_FLOOR))
                if quantity < 1:
                    reject("INSUFFICIENT_THREE_LEG_LIQUIDITY")
                    continue
                gross_rub = gross_units * unit * quantity
                fee_rub = costs_per_unit * quantity
                net_rub = gross_rub - fee_rub
                if net_rub <= 0:
                    reject("NONPOSITIVE_AFTER_ALL_DECLARED_COSTS")
                    continue
                positives += 1
                results.append(ModelCandidate(
                    first=ab.symbol, second=bc.symbol, direct=ac.symbol,
                    direction=direction, contracts=quantity, signed_gross_points=gross_units,
                    gross_rub=gross_rub, modeled_cost_rub=fee_rub,
                    modeled_net_rub=net_rub, observation_skew_ms=skew,
                ))
    results.sort(key=lambda c: (-c.modeled_net_rub, c.first, c.second, c.direct, c.direction))
    return {
        "status": "OFFLINE_MODEL_ONLY_NOT_AN_EXECUTION_SIGNAL",
        "instruments": len(items), "sequential_chains": chains,
        "complete_triangles": complete, "positive_model_directions": positives,
        "rejections": rejects,
        "candidates": [c.public_record() for c in results[:max_results]],
        "truncated": positives > max_results,
        "broker_orders": 0, "realized_pnl_rub": None,
        "execution_proven": False,
        "note": "Requires broker identity/lot/tariff verification, atomic fill proof and risk checks. Price anomaly is not realized PnL.",
    }