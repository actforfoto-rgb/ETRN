"""NORM ARB — public historical, cost-aware native-calendar spread prescreen.

RESEARCH ONLY. Inputs are independently captured public exchange snapshots. Do not
feed account/private brokerage information into public GitHub or logs.
No market orders, network calls, credentials or simulated fills.

A nominally positive BID->ASK width after fees is a mathematical upper bound
for TWO hypothetical passive fills, not a realized or executable strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import re
import math
from typing import Any

CODE = re.compile(r"^(.+)-(\d{1,2}\.\d{2})-(\d{1,2}\.\d{2})$")

class UnverifiedInput(ValueError):
    pass


def num(value: Any, *, strictly_positive: bool = False) -> Decimal:
    """No bool/NaN/Infinity/implicit fee defaults; float input is screening-only."""
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise UnverifiedInput("MISSING_DECIMAL")
    if isinstance(value, float) and not math.isfinite(value):
        raise UnverifiedInput("NONFINITE_DECIMAL")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise UnverifiedInput("BAD_DECIMAL") from exc
    if not result.is_finite() or (strictly_positive and result <= 0):
        raise UnverifiedInput("BAD_DECIMAL_RANGE")
    return result


def iso_z(value: Any) -> datetime:
    if not isinstance(value, str):
        raise UnverifiedInput("CAPTURE_TIME_MISSING")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UnverifiedInput("CAPTURE_TIME_INVALID") from exc
    if result.tzinfo is None:
        raise UnverifiedInput("CAPTURE_TIME_NAIVE")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class PublicCostScenario:
    broker_rate_each_futures_trade: Decimal
    exchange_maker_fee_rub_per_fill: Decimal
    nonnetting_capital_reserve_rub: Decimal
    studied_capital_rub: Decimal
    source: str
    label: str = "MODEL_ONLY_ACCOUNT_TARIFF_UNVERIFIED"

    def validate(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise UnverifiedInput("TARIFF_SOURCE_REQUIRED")
        if self.label != "MODEL_ONLY_ACCOUNT_TARIFF_UNVERIFIED":
            raise UnverifiedInput("MODEL_LABEL_CANNOT_BE_CHANGED_TO_LIVE")
        for key in ("broker_rate_each_futures_trade", "exchange_maker_fee_rub_per_fill",
                    "nonnetting_capital_reserve_rub", "studied_capital_rub"):
            v = num(getattr(self, key))
            if v < 0 or (key == "studied_capital_rub" and v == 0):
                raise UnverifiedInput("NEGATIVE_OR_ZERO_PARAMETER")
        if num(self.broker_rate_each_futures_trade) >= Decimal("0.2"):
            raise UnverifiedInput("RATE_ABSURD")


def _map_futures(rows: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        n = row.get("short_name")
        if not isinstance(n, str) or not n:
            continue
        if n in result:
            raise UnverifiedInput("DUPLICATED_FUTURE_SHORT_NAME")
        result[n] = row
    return result


def analyze_pair(spread: dict, futures: dict[str, dict], scenario: PublicCostScenario,
                 *, spread_captured_utc: str, futures_captured_utc: str) -> dict:
    scenario.validate()
    name = spread.get("symbol")
    if not isinstance(name, str):
        raise UnverifiedInput("SPREAD_NAME_MISSING")
    match = CODE.fullmatch(name)
    if not match:
        raise UnverifiedInput("SPREAD_CODE_UNMATCHED")
    near_name = f"{match[1]}-{match[2]}"
    far_name = f"{match[1]}-{match[3]}"
    near, far = futures.get(near_name), futures.get(far_name)
    if near is None or far is None:
        raise UnverifiedInput("LEG_NOT_FOUND")
    rp_near = num(near.get("stepprice_rub"), strictly_positive=True)/num(near.get("minstep"), strictly_positive=True)
    rp_far = num(far.get("stepprice_rub"), strictly_positive=True)/num(far.get("minstep"), strictly_positive=True)
    if abs(rp_near-rp_far)/max(rp_near, rp_far) >= Decimal("0.005"):
        raise UnverifiedInput("NONMATCHING_MONEY_MULTIPLIER")
    # Keep exact point value (using mid of two published underlying multipliers).
    factor = (rp_near+rp_far)/2
    bid, ask = num(spread.get("bid")), num(spread.get("ask"))
    if ask <= bid:
        raise UnverifiedInput("CROSSED_OR_EMPTY_SPREAD_BOOK")
    prices = [num(near.get("bid"),strictly_positive=True),num(near.get("ask"),strictly_positive=True),
              num(far.get("bid"),strictly_positive=True),num(far.get("ask"),strictly_positive=True)]
    if prices[0]>prices[1] or prices[2]>prices[3]:
        raise UnverifiedInput("CROSSED_OR_EMPTY_FUTURES_BOOK")
    near_mid = (prices[0]+prices[1])/2
    far_mid = (prices[2]+prices[3])/2
    broker_full = (near_mid+far_mid)*factor*num(scenario.broker_rate_each_futures_trade)*2
    fee_one_taker = num(near.get("buy_sell_fee_rub"))+num(far.get("buy_sell_fee_rub"))
    if fee_one_taker<0:
        raise UnverifiedInput("NEGATIVE_EXCHANGE_FEE")
    # Two passive spread fills each touch two futures; one maker fee per spread fill is an explicit scenario parameter.
    gross = (ask-bid)*factor
    both_maker = gross-broker_full-2*num(scenario.exchange_maker_fee_rub_per_fill)
    maker_taker = gross-broker_full-fee_one_taker-num(scenario.exchange_maker_fee_rub_per_fill)
    both_taker = gross-broker_full-2*fee_one_taker
    adverse_tick = max(num(near.get("minstep")),num(far.get("minstep")))*factor
    capital_proxy = num(near.get("margin_rub"))+num(far.get("margin_rub"))
    if capital_proxy<=0:
        raise UnverifiedInput("MARGIN_PUBLIC_PROXY_INVALID")
    lots = int(max(Decimal(0),(num(scenario.studied_capital_rub)-num(scenario.nonnetting_capital_reserve_rub))/capital_proxy).to_integral_value(rounding=ROUND_FLOOR))
    def cycles(target: Decimal, unit: Decimal) -> int | None:
        if unit<=0 or lots<=0:
            return None
        return int((target/(unit*lots)).to_integral_value(rounding=ROUND_CEILING))
    spread_at=iso_z(spread_captured_utc)
    futures_at=iso_z(futures_captured_utc)
    skew_seconds=abs((spread_at-futures_at).total_seconds())
    trades = num(spread.get("trades"))
    if trades < 0 or trades != trades.to_integral_value():
        raise UnverifiedInput("BAD_TRADE_COUNT")
    return {
        "symbol":name,"near":near.get("symbol"),"far":far.get("symbol"),
        "trades_reported_venue_day":int(trades),
        "gross_bid_ask_width_rub":str(gross),
        "public_broker_scenario_cost_rub":str(broker_full),
        "public_exchange_taker_each_spread_fill_rub":str(fee_one_taker),
        "ideal_two_maker_upper_rub":str(both_maker),
        "ideal_one_taker_upper_rub":str(maker_taker),
        "ideal_two_taker_upper_rub":str(both_taker),
        "one_adverse_leg_tick_rub":str(adverse_tick),
        "after_one_adverse_tick_two_maker_upper_rub":str(both_maker-adverse_tick),
        "public_two_leg_margin_sum_rub":str(capital_proxy),
        "unverified_lot_capacity_for_scenario":lots,
        "ideal_roundtrips_for_400rub_with_lots":cycles(Decimal(400),both_maker),
        "ideal_roundtrips_for_600rub_with_lots":cycles(Decimal(600),both_maker),
        "timestamp_skew_seconds":skew_seconds,
        "synchronized":False,
        "account_trade_route_verified":False,
        "account_fees_verified":False,
        "exchange_fills_proven":False,
        "actual_orders":0,
        "realized_pnl_rub":None,
        "model_status":scenario.label,
    }


def scan_public_snapshot(spread_source: dict, futures_source: dict, scenario: PublicCostScenario) -> dict:
    scenario.validate()
    spread_rows, futures_rows = spread_source.get("rows"),futures_source.get("rows")
    if not isinstance(spread_rows,list) or not isinstance(futures_rows,list):
        raise UnverifiedInput("SOURCE_ROWS_MISSING")
    # Fail closed on incomplete provenance rather than inventing same-time snapshots.
    spread_when = spread_source.get("retrieved_utc")
    futures_when = futures_source.get("sample_utc")
    iso_z(spread_when);iso_z(futures_when)
    lookup = _map_futures(futures_rows)
    rows, rejected = [],{}
    for spread in spread_rows:
        try:
            row = analyze_pair(spread,lookup,scenario,spread_captured_utc=spread_when,futures_captured_utc=futures_when)
            rows.append(row)
        except (UnverifiedInput,OverflowError) as exc:
            code = str(exc) or "INVALID_INPUT"
            rejected[code]=rejected.get(code,0)+1
    rows.sort(key=lambda x:(-int(x["trades_reported_venue_day"]),x["symbol"]))
    active=[r for r in rows if r["trades_reported_venue_day"]>=100]
    pos=[r for r in active if num(r["ideal_two_maker_upper_rub"])>0]
    return {
        "model":"NORM_PUBLIC_CALENDAR_ACCOUNT_UNVERIFIED",
        "spreads_input":len(spread_rows),"futures_input":len(futures_rows),
        "matched":len(rows),"rejected":rejected,
        "venue_trades_100_plus":len(active),
        "maker_upper_positive_with_100_trades":len(pos),
        "one_taker_upper_positive_with_100_trades":sum(num(r["ideal_one_taker_upper_rub"])>0 for r in active),
        "two_taker_upper_positive_with_100_trades":sum(num(r["ideal_two_taker_upper_rub"])>0 for r in active),
        "candidates":rows,
        "proven_profit_cycles":0,"actual_orders":0,"realized_profit_rub":None,
        "not_executable":True,"public_broker_rate_scenario":str(scenario.broker_rate_each_futures_trade),
        "fee_source":scenario.source,
    }