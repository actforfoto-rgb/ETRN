"""NORM 03B R22 — independent prospective funding evidence, no trading.

Entry and exit rules are frozen BEFORE settlement. State passes between
scheduled read-only GitHub Actions runs as immutable uploaded artifacts.
An archived quote is NOT a paper fill. Only one predeclared funding period.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import funding_gate as g
from execution_stress import evaluation as stress_evaluation

SCHEMA = "NORM03B_R22_PROSPECTIVE_FUNDING_V1"
MAX_CANDIDATES = 4
MAX_PENDING = 8
MAX_FINISHED = 240
EXIT_DELAY_MS = 60_000
EXIT_DEADLINE_MS = 50 * 60_000  # half-hourly observations; no discretionary extension
NEAR_MISS_FLOOR_USDT = g.D("-1.00")
MIN_SETTLEMENT_LEAD_MS = 60_000


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def sealed(obj, key):
    out = dict(obj)
    out[key] = sha(out)
    return out


def ensure_seal(obj, key):
    if not isinstance(obj, dict):
        raise ValueError("bad sealed object")
    x = dict(obj)
    val = x.pop(key, None)
    if val != sha(x):
        raise ValueError("evidence hash mismatch")


def new_state(run_id, now_ms):
    return dict(schema=SCHEMA, run_id=int(run_id), created_ms=int(now_ms),
                previous_state_sha=None, chain_status="FIRST_VALID_STATE",
                pending=[], finished=[], observations=[],
                net_realized_rub="0", virtual_cash_rub="50000",
                virtual_equity_rub="50000", paper_fills=0, real_orders=0)


def validate_state(state):
    ensure_seal(state, "state_sha256")
    if state["schema"] != SCHEMA:
        raise ValueError("unsupported state schema")
    if state.get("real_orders") != 0 or state.get("paper_fills") != 0:
        raise ValueError("state pretends executed orders or fills")
    if state.get("virtual_cash_rub") != "50000" or state.get("net_realized_rub") != "0":
        raise ValueError("virtual cash cannot be mutated by public quote research")
    if len(state.get("pending", [])) > MAX_PENDING:
        raise ValueError("pending capacity exceeded")
    ids = [x["event_id"] for x in state["pending"] + state["finished"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event id")
    for ev in state["pending"] + state["finished"]:
        ensure_seal(ev["anchor"], "anchor_sha256")
    return state


def rate_14_5bp(entry):
    stress = entry["one_funding_projected_execution_stress"]
    for scenario in stress["opportunity_cost_scenarios"]:
        if g.D(scenario["assumed_annual_alternative_yield"]) == g.D("0.14"):
            for cell in scenario["grid"]:
                if g.D(cell["extra_cost_bp_each_leg"]) == 5:
                    return g.D(cell["central_net_after_extra_cost_usdt"])
    raise ValueError("R21 stress configuration not available")


def precommit(scan, now_ms, run_id, existing):
    """Watch all qualified positive signals and near-miss NEGATIVE controls.

    This does not open even a virtual position: it freezes a hypothetical
    entry quote, and records every selected control to avoid winner bias.
    """
    already = {(e["anchor"]["pair"], e["anchor"]["funding_utc_s"])
               for e in existing["pending"] + existing["finished"]}
    ranked = []
    for item in scan.get("results", []):
        e = item.get("economic")
        if not isinstance(e, dict) or e.get("status") != "FORWARD_WATCH_NOT_FILL":
            continue
        try:
            pair = item["pair"]
            if not g.PAIR_RE.fullmatch(pair):
                continue
            time_of_funding = int(e["next_scheduled_funding_utc_s"])
            event_ms = time_of_funding * 1000
            clock = e["clock"]["exchange_ms"]
            first_ms = max(int(x) for x in clock)
            if not (first_ms <= now_ms < event_ms - MIN_SETTLEMENT_LEAD_MS):
                continue
            if (pair, time_of_funding) in already:
                continue
            candidate_net = rate_14_5bp(e)
            if candidate_net < NEAR_MISS_FLOOR_USDT:
                continue
            count = int(e["contract_count"])
            size = g.D(e["qty_base"])
            if count <= 0 or size <= 0:
                continue
            mult = size / g.D(count)
            linked = g.D(e["spot_entry_usdt"]) + g.D(e["future_short_notional_usdt"])
            if linked <= 0:
                continue
            label = ("POSITIVE_STRESSED_SIGNAL" if candidate_net > 0
                     else "NEGATIVE_CONTROL_NOT_ENTRY")
            raw = dict(
                schema="NORM03B_R22_FROZEN_EVENT_V1", pair=pair, signal_run=int(run_id),
                source_market_ms=first_ms, source_received_ms=now_ms,
                spot_quote_exchange_ms=int(clock[0]),
                futures_quote_exchange_ms=int(clock[1]),
                funding_utc_s=time_of_funding,
                exit_not_before_ms=event_ms+EXIT_DELAY_MS,
                exit_deadline_ms=event_ms+EXIT_DEADLINE_MS,
                exit_rule="FIRST_VALID_OBSERVED_PAIRED_L2_AFTER_FUNDING_PLUS_60S_BEFORE_50MIN",
                contract_count=count, quantity_base=str(size),
                multiplier_base_per_contract=str(mult),
                spot_buy_cost_usdt=e["spot_entry_usdt"],
                future_short_proceeds_usdt=e["future_short_notional_usdt"],
                linked_capital_usdt=str(linked),
                projected_one_payment_usdt=e["quoted_single_payment_usdt"],
                projected_14pct_5bp_net_usdt=str(candidate_net),
                predetermined_14pct_apr="0.14", predetermined_extra_bp_each_leg="5",
                selection_status=label,
                source_digest=item.get("source_digests", []),
                actual_execution=False, owner_eligibility_verified=False)
            anchor = sealed(raw, "anchor_sha256")
            eid = sha({"pair":pair,"funding":time_of_funding,
                       "anchor_sha256":anchor["anchor_sha256"]})[:24]
            ranked.append((candidate_net, {"event_id":eid, "anchor":anchor,
                            "state":"FROZEN_AWAIT_NEXT_SETTLEMENT",
                            "attempted_observations":[]}))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            continue
    ranked.sort(key=lambda t: (t[0], t[1]["event_id"]), reverse=True)
    existing_positive = [e for _,e in ranked if e["anchor"]["selection_status"]=="POSITIVE_STRESSED_SIGNAL"]
    controls = [e for _,e in ranked if e["anchor"]["selection_status"]!="POSITIVE_STRESSED_SIGNAL"]
    choices = (existing_positive[:MAX_CANDIDATES] + controls[:2])[:MAX_CANDIDATES]
    return choices[:max(0,MAX_PENDING-len(existing["pending"]))]


def fetch_exit(pair, event_s):
    """Public exchange reads only, independent receipts/hashes."""
    if not g.PAIR_RE.fullmatch(pair):
        raise ValueError("invalid pair")
    prefix = g.ROOT
    endpoints = {
        "spot":f"{prefix}/spot/order_book?currency_pair={pair}&limit=10&with_id=true",
        "future":f"{prefix}/futures/usdt/order_book?contract={pair}&limit=10&with_id=true",
        "history":f"{prefix}/futures/usdt/funding_rate?contract={pair}&limit=100",
        "contract":f"{prefix}/futures/usdt/contracts/{pair}",
        "mark":f"{prefix}/futures/usdt/candlesticks?contract=mark_{pair}&interval=1m&from={event_s-120}&to={event_s}"
    }
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={k:pool.submit(g.public_json,url) for k,url in endpoints.items()}
        fetched={k:v.result() for k,v in futures.items()}
    return {k:v[0] for k,v in fetched.items()}, {
        k:{"received_ms":v[1],"body_sha256":v[2]} for k,v in fetched.items()}


def prospective_exit(anchor, data, receipts, now_ms):
    """Returns a closed quote PROXY only if no future information is needed.

    The first valid quote *actually collected* within the frozen exit
    window is final. Later prices cannot replace it.
    """
    ensure_seal(anchor, "anchor_sha256")
    target_ms=int(anchor["exit_not_before_ms"])
    end_ms=int(anchor["exit_deadline_ms"])
    if now_ms < target_ms:
        return {"status":"WAIT_FOR_PRECOMMITTED_EXIT_TIME"}
    if now_ms > end_ms:
        return {"status":"MISSED_PRECOMMITTED_EXIT_DEADLINE"}
    pair=anchor["pair"]
    contract=data["contract"]
    if not isinstance(contract, dict):
        return {"status":"MISSING_CONTRACT_META"}
    if (contract.get("name")!=pair or contract.get("type")!="direct" or
        contract.get("in_delisting") is not False or
        contract.get("status", "trading")!="trading"):
        return {"status":"CONTRACT_IDENTITY_NOT_VERIFIED"}
    if g.D(contract.get("quanto_multiplier","0"))!=g.D(anchor["multiplier_base_per_contract"]):
        return {"status":"CONTRACT_MULTIPLIER_CHANGED"}
    sb=data["spot"]; fb=data["future"]
    valid,clocks=g.valid_time(sb,fb,receipts["spot"]["received_ms"],
                              receipts["future"]["received_ms"])
    if not valid:
        return {"status":"INVALID_L2_TIME_OR_SKEW","clock":clocks}
    quote_time=min(clocks["exchange_ms"])
    if quote_time < target_ms or quote_time > end_ms:
        return {"status":"QUOTE_OUTSIDE_FROZEN_EXIT_WINDOW","clock":clocks}
    sbs=g.levels(sb,False,"bids")
    sas=g.levels(sb,False,"asks")
    fbs=g.levels(fb,True,"bids")
    fas=g.levels(fb,True,"asks")
    if not all([sbs,sas,fbs,fas]) or sbs[0][0]>=sas[0][0] or fbs[0][0]>=fas[0][0]:
        return {"status":"INVALID_EXIT_DEPTH_OR_CROSSED_BOOK"}
    qty=g.D(anchor["quantity_base"])
    mult=g.D(anchor["multiplier_base_per_contract"])
    sale=g.walk(sbs,qty)
    cover=g.walk(fas,qty,mult)
    if sale is None or cover is None:
        return {"status":"INSUFFICIENT_EXIT_DEPTH"}
    historical = data["history"]
    if not isinstance(historical,list):
        return {"status":"BAD_SETTLEMENT_HISTORY"}
    event_s=int(anchor["funding_utc_s"])
    matched = {}
    for item in historical:
        try:
            t=int(item["t"]); rate=g.D(item["r"])
            if t==event_s and abs(rate)<=g.D(".1"):
                matched.setdefault(t,rate)
        except (KeyError,TypeError,ValueError):
            continue
    if event_s not in matched:
        return {"status":"PAID_RATE_NOT_YET_AVAILABLE"}
    marks=data["mark"]
    if not isinstance(marks,list):
        return {"status":"BAD_MARK_CANDLES"}
    mark = None
    for item in marks:
        try:
            if int(item["t"]) == event_s-60:
                low, high, close=(g.D(item[k]) for k in ("l","h","c"))
                if low<=close<=high and low>0:
                    mark=(low, high, close)
                break
        except (ValueError,KeyError,TypeError):
            continue
    if mark is None:
        return {"status":"PRE_SETTLEMENT_MARK_CANDLE_UNAVAILABLE"}
    # Honest mark-price approximation: no exact settlement mark is claimed.
    fund=qty*mark[2]*matched[event_s]
    low = qty*(mark[0] if matched[event_s]>=0 else mark[1])*matched[event_s]
    high= qty*(mark[1] if matched[event_s]>=0 else mark[0])*matched[event_s]
    entry_spot=g.D(anchor["spot_buy_cost_usdt"])
    entry_future=g.D(anchor["future_short_proceeds_usdt"])
    gross=(sale-entry_spot)+(entry_future-cover)
    fees=g.SPOT_TAKER*(entry_spot+sale)+g.FUT_TAKER*(entry_future+cover)
    base=g.D(anchor["linked_capital_usdt"])
    central = stress_evaluation(
        net_usdt=gross-fees+fund, funding_low_proxy_net_usdt=gross-fees+low,
        leg_turnover_usdt=(entry_spot,entry_future,sale,cover),
        linked_capital_usdt=base, start_utc_ms=int(anchor["source_received_ms"]),
        end_utc_ms=quote_time)
    stressed = next(
        cell for scenario in central["opportunity_cost_scenarios"]
        if g.D(scenario["assumed_annual_alternative_yield"])==g.D(".14")
        for cell in scenario["grid"] if g.D(cell["extra_cost_bp_each_leg"])==5)
    return {
        "status":"FULL_HISTORICAL_L2_QUOTE_PROXY_NOT_A_FILL",
        "net_after_5bp_and_14pct_usdt":stressed["central_net_after_extra_cost_usdt"],
        "low_mark_proxy_net_after_5bp_and_14pct_usdt":stressed["low_funding_proxy_net_after_extra_cost_usdt"],
        "central_positive_after_stress":stressed["strictly_positive_central"],
        "low_proxy_positive_after_stress":stressed["strictly_positive_low_funding_proxy"],
        "paid_funding_rate":str(matched[event_s]),
        "funding_using_one_minute_pre_settlement_mark_usdt":str(fund),
        "funding_low_high_proxy_usdt":[str(low),str(high)],
        "gross_price_pnl_usdt":str(gross),
        "four_base_taker_fees_usdt":str(fees),
        "spot_sale_depth_usdt":str(sale),
        "future_cover_depth_usdt":str(cover),
        "exit_book_market_ms":quote_time,
        "linked_capital_scenario_usdt":str(base),
        "independently_verified_fills":0, "real_orders":0,
        "settlement_mark_not_exact":True, "owner_eligible":False,
        "source_sha256":{k:v["body_sha256"] for k,v in receipts.items()}
    }


def evolve(prior, scan, now_ms, run_id, source=None):
    """Pure state machine apart from injected source(anchor)->data,receipts.

    Once recorded as resolved, an event is never repriced at a later exit.
    """
    if prior is None:
        state=new_state(run_id,now_ms)
    else:
        validated=validate_state(prior)
        state={k:v for k,v in validated.items() if k!="state_sha256"}
        if int(run_id)<=int(state["run_id"]):
            raise ValueError("cannot replay current or older run onto itself")
        state["previous_state_sha"]=prior["state_sha256"]
        state["chain_status"]="VERIFIED_PREVIOUS_ARTIFACT"
        state["run_id"]=int(run_id)
        state["created_ms"]=int(now_ms)
    source=source or (lambda a: fetch_exit(a["pair"],int(a["funding_utc_s"])))
    still=[]
    for e in state["pending"]:
        a=e["anchor"]
        when=int(a["exit_not_before_ms"])
        if now_ms < when:
            still.append(e)
            continue
        if now_ms > int(a["exit_deadline_ms"]):
            e=dict(e, state="MISSED_DEADLINE_NO_EXECUTABLE_QUOTE")
            state["finished"].append(e)
            continue
        try:
            data,receipts=source(a)
            res=prospective_exit(a,data,receipts,now_ms)
        except (OSError,ValueError,TypeError,KeyError,ArithmeticError) as exc:
            res={"status":"DATA_UNAVAILABLE","error_type":type(exc).__name__}
        e=dict(e)
        e["attempted_observations"]=(e.get("attempted_observations",[])+
             [{"run_id":int(run_id),"asof_ms":int(now_ms),"status":res["status"]}])[-12:]
        if res["status"]=="FULL_HISTORICAL_L2_QUOTE_PROXY_NOT_A_FILL":
            e["state"]="PROXY_RESOLVED_NOT_EXECUTED"
            e["result"]=res
            state["finished"].append(e)
        else:
            still.append(e)
    state["pending"]=still
    newly=precommit(scan,now_ms,run_id,state)
    state["pending"].extend(newly)
    state["finished"]=state["finished"][-MAX_FINISHED:]
    state["observations"]=(state.get("observations",[])+[{
        "run_id":int(run_id),"utc_ms":int(now_ms),
        "scan_status":scan.get("status","SCAN_COMPLETED"),
        "scan_source_sha256":sha(scan),
        "frozen":len(newly),
        "pending":len(state["pending"]),
        "finished":len(state["finished"]),
        "positive_quote_proxies":sum(x.get("result",{}).get(
            "central_positive_after_stress",False) for x in state["finished"]),
        "low_bound_positive_quote_proxies":sum(x.get("result",{}).get(
            "low_proxy_positive_after_stress",False) for x in state["finished"])
    }])[-MAX_FINISHED:]
    state["virtual_cash_rub"]="50000"
    state["virtual_equity_rub"]="50000"
    state["net_realized_rub"]="0"
    state["paper_fills"]=0
    state["real_orders"]=0
    return sealed(state,"state_sha256")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--scan",required=True,type=Path)
    ap.add_argument("--previous",required=True,type=Path)
    ap.add_argument("--out",required=True,type=Path)
    ap.add_argument("--summary",required=True,type=Path)
    args=ap.parse_args()
    scan=json.loads(args.scan.read_text(encoding="utf-8"))
    prior=json.loads(args.previous.read_text(encoding="utf-8")) if args.previous.exists() else None
    now=int(time.time()*1000)
    run_id=int(os.getenv("GITHUB_RUN_ID", str(now)))
    new=evolve(prior,scan,now,run_id)
    summary=dict(
        schema=SCHEMA, state_sha256=new["state_sha256"],run_id=run_id,
        chain_status=new["chain_status"],
        pending=len(new["pending"]),finished=len(new["finished"]),
        controls=sum(e["anchor"]["selection_status"]=="NEGATIVE_CONTROL_NOT_ENTRY"
                     for e in new["pending"]),
        positive_stressed_entry_screens=sum(
            e["anchor"]["selection_status"]=="POSITIVE_STRESSED_SIGNAL"
            for e in new["pending"]),
        resolved_stress_positive=sum(e.get("result",{}).get(
            "central_positive_after_stress",False) for e in new["finished"]),
        resolved_low_bound_positive=sum(e.get("result",{}).get(
            "low_proxy_positive_after_stress",False) for e in new["finished"]),
        real_orders=0,paper_fills=0,virtual_equity_rub=50000,
        live_eligible=False,
        rule="FIRST_VALID_PAIRED_QUOTE_AFTER_SETTLEMENT_PLUS_60S_BEFORE_50MIN")
    for p,obj in ((args.out,new),(args.summary,summary)):
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(obj,indent=2,ensure_ascii=False,sort_keys=True),
                     encoding="utf-8")
    print(json.dumps(summary,sort_keys=True))


if __name__=="__main__":
    main()
