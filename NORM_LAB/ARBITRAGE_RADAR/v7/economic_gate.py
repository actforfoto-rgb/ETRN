"""NORM ARB: money-first, non-trading economic gate for native MOEX calendars.

No hardcoded Finam fees, no invented capital requirements, no paper profits
mislabelled as actual profits. A quote width is merely the upper bound from
buying passively at BID and selling passively at ASK, not a completed cycle.

Every cost must be verified for this specific account, both futures legs and
both directions. Prices use native-calendar spread points (may be negative).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any

class EconomicEvidenceMissing(ValueError):
    """Fail-closed: cannot calculate net cash from unknown broker conditions."""


def rub(value:Any, *, positive:bool=False) -> Decimal:
    if isinstance(value,bool) or value is None or not isinstance(value,(str,int,Decimal)):
        raise EconomicEvidenceMissing('UNVERIFIED_MONETARY_PARAMETER')
    try: v=Decimal(str(value))
    except (ValueError,InvalidOperation): raise EconomicEvidenceMissing('INVALID_MONETARY_PARAMETER') from None
    if not v.is_finite() or v<0 or (positive and v<=0):
        raise EconomicEvidenceMissing('INVALID_MONETARY_PARAMETER')
    return v

@dataclass(frozen=True)
class AccountEconomics:
    symbol:str
    rub_per_spread_point:Decimal
    account_initial_margin_rub:Decimal
    broker_buy_rub:Decimal
    broker_sell_rub:Decimal
    exchange_buy_rub:Decimal
    exchange_sell_rub:Decimal
    execution_stress_rub_each_way:Decimal
    reserve_rub:Decimal
    verified_source:str
    verified_account:bool
    currency:str='RUB'

    def validate(self)->None:
        if type(self.symbol) is not str or not self.symbol.endswith('@RTSX'):
            raise EconomicEvidenceMissing('NO_VERIFIED_SYMBOL')
        if self.currency!='RUB' or self.verified_account is not True:
            raise EconomicEvidenceMissing('ACCOUNT_COST_EVIDENCE_NOT_VERIFIED')
        if type(self.verified_source) is not str or not self.verified_source.strip():
            raise EconomicEvidenceMissing('COST_SOURCE_MISSING')
        for name in ('rub_per_spread_point','account_initial_margin_rub'):
            rub(getattr(self,name),positive=True)
        for name in ('broker_buy_rub','broker_sell_rub','exchange_buy_rub','exchange_sell_rub',
                     'execution_stress_rub_each_way','reserve_rub'):
            rub(getattr(self,name))


def cost_per_roundtrip(ev:AccountEconomics)->Decimal:
    ev.validate()
    return (rub(ev.broker_buy_rub)+rub(ev.broker_sell_rub)+
            rub(ev.exchange_buy_rub)+rub(ev.exchange_sell_rub)+
            2*rub(ev.execution_stress_rub_each_way))


def quote_width_upper_bound(book:dict[str,Any], costs:AccountEconomics|None,
                            capital_rub:Any)->dict[str,Any]:
    """Maximum hypothetical profit if BOTH passive sides magically filled.

    This does not decide whether a passive order fills or whether an exit exists;
    execution/fill/queue/inventory risk is still open. Results never get a
    'realized_profit' value. Unverified account costs produce NO NUMBER.
    """
    meta={'symbol':book.get('symbol') if isinstance(book,dict) else None,
          'model':'IDEAL_MAKER_BOTH_SIDES_UPPER_BOUND',
          'executable':False,'realized_profit_rub':None,'orders_sent':0,
          'fill_probability_estimated':False,'continuity_verified':False}
    if costs is None:
        return {**meta,'status':'BLOCKED_ACCOUNT_COSTS_UNKNOWN','net_upper_bound_rub':None}
    try:
        costs.validate()
        capital=rub(capital_rub,positive=True)
    except EconomicEvidenceMissing as exc:
        return {**meta,'status':str(exc),'net_upper_bound_rub':None}
    if not isinstance(book,dict) or book.get('symbol')!=costs.symbol:
        return {**meta,'status':'BOOK_SYMBOL_MISMATCH','net_upper_bound_rub':None}
    if not book.get('ws_snapshot_received') or not book.get('market_data_observed'):
        return {**meta,'status':'NO_VERIFIED_WS_BOOK_SNAPSHOT','net_upper_bound_rub':None}
    if book.get('bid') is None or book.get('ask') is None:
        return {**meta,'status':'NO_TWO_SIDED_BOOK','net_upper_bound_rub':None}
    # Dates and row timestamps are not proof of gap-free market-feed continuity.
    # A bad source-status stops even idealized modeling, not just LIVE.
    if book.get('status') not in ('TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF',):
        return {**meta,'status':'BOOK_QUALITY_UNVERIFIED','net_upper_bound_rub':None}
    try:
        bid=Decimal(str(book['bid']));ask=Decimal(str(book['ask']))
        bid_qty=rub(book.get('bid_qty'),positive=True)
        ask_qty=rub(book.get('ask_qty'),positive=True)
        if not bid.is_finite() or not ask.is_finite() or bid>=ask:
            raise EconomicEvidenceMissing('INVALID_BOOK_PRICE')
        usable=capital-rub(costs.reserve_rub)
        n_margin=int((usable/rub(costs.account_initial_margin_rub,positive=True)).to_integral_value(rounding=ROUND_FLOOR)) if usable>0 else 0
        # Quote sizes are lots, not fractional cash units. Min depth at BOTH sides.
        n=max(0,min(n_margin,int(bid_qty),int(ask_qty)))
        if n<1:
            return {**meta,'status':'CAPITAL_OR_DEPTH_INSUFFICIENT',
                    'net_upper_bound_rub':None,'tradable_lots':0}
        width=ask-bid
        gross=width*rub(costs.rub_per_spread_point,positive=True)*n
        roundtrip=cost_per_roundtrip(costs)*n
        net=gross-roundtrip
        return {**meta,'status':('MODEL_UPPER_BOUND_POSITIVE_UNPROVEN_FILL' if net>0
                                 else 'MODEL_UPPER_BOUND_NOT_POSITIVE'),
                'tradable_lots':n,'width_points':str(width),
                'gross_upper_bound_rub':str(gross),
                'roundtrip_costs_rub':str(roundtrip),
                'net_upper_bound_rub':str(net),
                'capital_held_rub':str(rub(costs.account_initial_margin_rub)*n),
                'actual_fills':0}
    except (EconomicEvidenceMissing,ValueError,InvalidOperation,OverflowError,TypeError):
        return {**meta,'status':'INVALID_BOOK_OR_COST_VALUES','net_upper_bound_rub':None}


@dataclass
class TradeAllocationLedger:
    """One ledger for ONE EXCHANGE TRADING SESSION, shared across ALL shadow orders.

    Uses public exchange trade ids, not WebSocket message ids or connection ids.
    A reconnect must reuse the same trading-session ledger, never start a fresh
    budget for prints already observed. This is still NOT evidence of a fill.
    """
    session_id: str
    used_volume: dict[tuple[str, str], Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip() or len(self.session_id) > 120:
            raise EconomicEvidenceMissing('INVALID_TRADING_SESSION')


def conservative_trade_through(
    order_side: str, limit_price: Any, posted_at: datetime,
    prints: list[dict[str, Any]], min_latency_ms: int = 250, *,
    instrument_symbol: str | None = None,
    requested_volume: Any = None,
    ledger: TradeAllocationLedger | None = None,
    volume_units_verified: bool = False,
) -> dict[str, Any]:
    """Conservative, one-to-one, trade-through V2; NEVER a broker fill.

    All prints MUST carry matching 'symbol' and 'session_id', a hashed PUBLIC
    exchange trade id, and positive volume in a VERIFIED comparable unit.
    Reused trades are exhausted across orders via the shared session ledger.
    An incomplete hypothetical order cannot reserve evidence or count as filled.
    """
    result = {'status': 'NO_TRADE_THROUGH', 'actual_fill_proven': False,
              'queue_priority_verified': False, 'model': 'SHADOW_TRADE_THROUGH_LEDGER_V2',
              'orders_sent': 0, 'realized_pnl_rub': None, 'executable': False}
    if order_side not in ('BUY', 'SELL') or not isinstance(posted_at, datetime) or posted_at.tzinfo is None:
        return {**result, 'status': 'INVALID_SHADOW_ORDER'}
    if type(min_latency_ms) is not int or min_latency_ms < 1:
        return {**result, 'status': 'INVALID_LATENCY_BOUND'}
    try:
        if isinstance(limit_price, (bool, float)):
            raise EconomicEvidenceMissing('INVALID_PRICE')
        price = Decimal(str(limit_price))
        if not price.is_finite():
            raise EconomicEvidenceMissing('INVALID_PRICE')
    except (InvalidOperation, ValueError, TypeError, EconomicEvidenceMissing):
        return {**result, 'status': 'INVALID_PRICE'}
    if not isinstance(instrument_symbol, str) or not instrument_symbol.endswith('@RTSX'):
        return {**result, 'status': 'INSTRUMENT_CONTEXT_REQUIRED'}
    if not isinstance(ledger, TradeAllocationLedger):
        return {**result, 'status': 'SHARED_SESSION_LEDGER_REQUIRED'}
    if volume_units_verified is not True:
        return {**result, 'status': 'PRINT_VOLUME_UNITS_UNVERIFIED'}
    try:
        needed = rub(requested_volume, positive=True)
    except EconomicEvidenceMissing:
        return {**result, 'status': 'INVALID_REQUESTED_VOLUME'}
    if not isinstance(prints, list):
        return {**result, 'status': 'INVALID_TRADE_LIST'}
    threshold = posted_at.astimezone(timezone.utc).timestamp() + min_latency_ms / 1000

    # Normalize before reserving: duplicate ids are ONE exchange print, never
    # two independent liquidity allocations. Conflicting duplicates are dropped.
    found: dict[str, tuple[datetime, Decimal, Decimal]] = {}
    conflicted: set[str] = set()
    for trade in prints:
        if not isinstance(trade, dict):
            continue
        if trade.get('symbol') != instrument_symbol or trade.get('session_id') != ledger.session_id:
            continue
        tid = trade.get('public_trade_id_hash')
        if not isinstance(tid, str) or len(tid) != 24 or any(c not in '0123456789abcdef' for c in tid):
            continue  # no stable identity => no claimable volume
        try:
            ts = trade['timestamp']
            if not isinstance(ts, str):
                continue
            when = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            if when.tzinfo is None or when.timestamp() <= threshold:
                continue
            p = Decimal(str(trade['price']))
            size = rub(trade['size'], positive=True)
            if not p.is_finite():
                continue
        except (KeyError, ValueError, InvalidOperation, TypeError, EconomicEvidenceMissing, OverflowError):
            continue
        if not ((order_side == 'BUY' and p < price) or (order_side == 'SELL' and p > price)):
            continue  # price touch is NOT trade-through
        normalized = (when.astimezone(timezone.utc), p, size)
        if tid in found and found[tid] != normalized:
            conflicted.add(tid)
        else:
            found[tid] = normalized

    available: list[tuple[tuple[str, str], Decimal, datetime]] = []
    for tid, (when, _p, size) in sorted(found.items(), key=lambda x: (x[1][0], x[0])):
        if tid in conflicted:
            continue
        key = (instrument_symbol, tid)
        committed = ledger.used_volume.get(key, Decimal(0))
        if not isinstance(committed, Decimal) or not committed.is_finite() or committed < 0 or committed > size:
            return {**result, 'status': 'LEDGER_VOLUME_INCONSISTENT'}
        if size > committed:
            available.append((key, size - committed, when))

    if not available:
        return result
    if sum((x[1] for x in available), Decimal(0)) < needed:
        return {**result, 'status': 'INSUFFICIENT_UNCONSUMED_TRADE_VOLUME'}

    # Atomic reservation only after FULL requested hypothetical volume exists.
    remaining = needed
    selected = 0
    last_when = None
    for key, volume, when in available:
        take = min(volume, remaining)
        if take > 0:
            ledger.used_volume[key] = ledger.used_volume.get(key, Decimal(0)) + take
            remaining -= take
            selected += 1
            last_when = when
        if remaining == 0:
            break
    return {**result, 'status': 'TRADE_THROUGH_PROXY_NO_FILL_PROOF',
            'instrument_symbol': instrument_symbol, 'trading_session': ledger.session_id,
            'allocated_proxy_volume': str(needed), 'independent_trade_ids_used': selected,
            'exchange_print_timestamp': last_when.isoformat() if last_when else None}
