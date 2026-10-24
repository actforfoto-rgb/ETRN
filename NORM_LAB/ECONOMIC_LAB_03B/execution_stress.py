"""NORM 03B — deterministic full-turnover cost and opportunity-cost veto.

Read-only arithmetic. A positive *historical* or *quoted* proxy NEVER becomes a
fill or trading authorisation. Annual capital rates, fees and slippage are
explicit conditional scenarios, not verified user-account terms.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

DEFAULT_EXTRA_SLIPPAGE_BP = ("0", "1", "2", "4", "5", "10")
DEFAULT_OPPORTUNITY_APR = ("0", "0.14", "0.18")
YEAR_MS = Decimal(365 * 24 * 3600 * 1000)


def number(value):
    try:
        x=Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("not a decimal") from exc
    if not x.is_finite():
        raise ValueError("not finite")
    return x


def evaluation(*, net_usdt, leg_turnover_usdt, linked_capital_usdt,
               start_utc_ms, end_utc_ms,
               funding_low_proxy_net_usdt=None,
               extra_slippage_bp=DEFAULT_EXTRA_SLIPPAGE_BP,
               annual_opportunity_apr=DEFAULT_OPPORTUNITY_APR):
    """Return the exact zero-edge frontier of a CONDITIONAL hedged cycle.

    Turnover includes FOUR nonnegative gross cash legs: buy spot, sell perp,
    sell spot, cover perp. Additional bp are charged on each of these amounts
    above the already included order-book depth and base fee assumptions.
    Linked capital is an EXPLICIT scenario, not an observed margin requirement.
    A min/max funding candle range is a sensitivity proxy, never guaranteed.
    """
    legs=tuple(number(x) for x in leg_turnover_usdt)
    if len(legs)!=4 or any(x<=0 for x in legs):
        raise ValueError("exactly four strictly positive cash legs required")
    turnover=sum(legs,Decimal(0))
    capital=number(linked_capital_usdt)
    if capital<=0:
        raise ValueError("scenario linked capital must be positive")
    start=int(start_utc_ms); end=int(end_utc_ms)
    if start<0 or end<=start:
        raise ValueError("bad interval")
    duration=Decimal(end-start)
    quoted_net=number(net_usdt)
    lower=number(funding_low_proxy_net_usdt) if funding_low_proxy_net_usdt is not None else None
    if lower is not None and lower>quoted_net:
        raise ValueError("worst-case proxy cannot exceed central net")
    bps=tuple(number(z) for z in extra_slippage_bp)
    aprs=tuple(number(z) for z in annual_opportunity_apr)
    if not bps or not aprs or any(x<0 or x>1000 for x in bps) or any(x<0 or x>1 for x in aprs):
        raise ValueError("invalid stress scenario")
    tables=[]
    for rate in aprs:
        opp=capital*rate*duration/YEAR_MS
        remaining=quoted_net-opp
        worst_remaining=(lower-opp if lower is not None else None)
        cells=[]
        for bp in bps:
            slip=turnover*bp/Decimal(10000)
            cells.append({
                "extra_cost_bp_each_leg":str(bp),
                "extra_total_slippage_usdt":str(slip),
                "central_net_after_extra_cost_usdt":str(remaining-slip),
                "low_funding_proxy_net_after_extra_cost_usdt":str(worst_remaining-slip) if worst_remaining is not None else None,
                "strictly_positive_central":remaining-slip>0,
                "strictly_positive_low_funding_proxy":worst_remaining-slip>0 if worst_remaining is not None else None
            })
        tables.append({
            "assumed_annual_alternative_yield":str(rate),
            "opportunity_cost_usdt":str(opp),
            "central_break_even_extra_bp_each_leg":str(remaining/turnover*Decimal(10000)),
            "low_funding_break_even_extra_bp_each_leg":str(worst_remaining/turnover*Decimal(10000)) if worst_remaining is not None else None,
            "grid":cells
        })
    base_break_even=quoted_net/turnover*Decimal(10000)
    stress_5=quoted_net-turnover*Decimal(5)/Decimal(10000)
    verdict=(
        "NONPOSITIVE_PRE_STRESS_MODEL" if quoted_net<=0 else
        "FRAGILE_UNDER_5BP_EXTRA_EACH_LEG" if stress_5<=0 else
        "RESEARCH_POSITIVE_ONLY_OWNER_EXECUTION_UNVERIFIED"
    )
    return {
        "schema":"NORM03B_FOUR_LEG_EXECUTION_STRESS_V1",
        "status":verdict,
        "four_cash_legs_usdt":[str(x) for x in legs],
        "turnover_usdt":str(turnover),
        "linked_capital_scenario_usdt":str(capital),
        "held_ms":end-start,
        "held_hours":str(duration/Decimal(3600000)),
        "baseline_net_usdt":str(quoted_net),
        "funding_low_proxy_net_usdt":str(lower) if lower is not None else None,
        "zero_opportunity_cost_break_even_extra_bp_each_leg":str(base_break_even),
        "assuming_5bp_extra_each_leg_net_usdt":str(stress_5),
        "opportunity_cost_scenarios":tables,
        "actual_executed_orders":0,
        "verified_venue_account_eligibility":False,
        "claim_real_profit":False,
        "limits":[
            "Bid/ask depth is a quote, not proof of execution or latency",
            "Owner trading tariffs, liquidation risk, conversion and margin not verified",
            "Annual opportunity yields and full two-leg prefunding are scenarios",
            "Missing per-venue simultaneous execution can cause large unhedged loss",
            "Positive historical/counterfactual net is not an independent prospective filled cycle"
        ]
    }
