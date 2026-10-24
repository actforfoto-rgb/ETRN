"""NORM 03B M2: causally revalue the frozen IRYS spot+perp funding signal.

Only public Gate data are read. The 23 Oct quote is NOT a fill. Its historical
entry book is frozen by a source-artifact digest; subsequent exits and SETTLED
funding are purely a hypothetical counterfactual, never booked as PnL.
"""
from __future__ import annotations
import json
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

ANCHOR_FILE=Path(__file__).with_name("m2_irys_fixed_anchor.json")
QUOTE_MAX_AGE_MS=15000
PAIR="IRYS_USDT"


def evaluate(anchor, spot_book, future_book, history, spot_receive_ms,
             future_receive_ms, asof_ms):
    import funding_gate as g
    if anchor.get("source_entry_was_filled") is not False or (
            anchor.get("real_orders")!=0 or anchor.get("virtual_paper_fills")!=0):
        raise ValueError("anchor is not a zero-fill research signal")
    if anchor.get("pair")!=PAIR:
        raise ValueError("wrong frozen instrument")
    ok,clock=g.valid_time(spot_book,future_book,spot_receive_ms,future_receive_ms)
    if not ok:
        return {"status":"STALE_OR_DESYNCHRONIZED_EXIT_BOOK","clock":clock}
    spot_ms,future_ms=clock["exchange_ms"]
    cutoff=min(spot_ms,future_ms)
    if cutoff<=int(anchor["first_observed_utc_ms"]):
        return {"status":"EXIT_NOT_AFTER_SIGNAL","clock":clock}
    if not -1000<=asof_ms-spot_ms<=QUOTE_MAX_AGE_MS or (
            not -1000<=asof_ms-future_ms<=QUOTE_MAX_AGE_MS):
        return {"status":"EXIT_BOOK_NOT_CURRENT","clock":clock}
    sb=g.levels(spot_book,False,"bids")
    sa=g.levels(spot_book,False,"asks")
    fb=g.levels(future_book,True,"bids")
    fa=g.levels(future_book,True,"asks")
    if not (sb and sa and fb and fa):
        return {"status":"MISSING_BID_ASK","clock":clock}
    if sb[0][0]>=sa[0][0] or fb[0][0]>=fa[0][0]:
        return {"status":"CROSSED_EXIT_BOOK","clock":clock}
    qty=g.D(anchor["qty_base"])
    mult=g.D(anchor["contract_multiplier"])
    spot_sale=g.walk(sb,qty)
    future_buyback=g.walk(fa,qty,mult)
    if spot_sale is None or future_buyback is None:
        return {"status":"INSUFFICIENT_REAL_EXIT_DEPTH","clock":clock}
    valid_rates=[]
    before_signal=0
    after_exit=0
    for item in history:
        try:
            tm=int(item["t"])*1000
            rate=g.D(item["r"])
            if abs(rate)>Decimal(".1"):continue
            if tm<=int(anchor["first_observed_utc_ms"]):
                before_signal+=1
            elif tm>cutoff:
                after_exit+=1
            else:
                valid_rates.append((tm,rate))
        except (KeyError,ValueError,TypeError):
            continue
    unique={}
    for tm,rate in sorted(valid_rates):
        unique.setdefault(tm,rate)
    total_rate=sum(unique.values(),Decimal(0))
    spot_entry=g.D(anchor["spot_buy_entry_usdt"])
    future_entry=g.D(anchor["future_short_entry_usdt"])
    gross=(spot_sale-spot_entry)+(future_entry-future_buyback)
    fees=g.SPOT_TAKER*(spot_entry+spot_sale)+g.FUT_TAKER*(future_entry+future_buyback)
    funding_proxy=total_rate*future_entry
    mark=gross-fees+funding_proxy
    return {
        "status":"CAUSAL_UNFILLED_COUNTERFACTUAL_MARK",
        "source_signal_run":int(anchor["source_run_id"]),
        "first_observed_utc_ms":int(anchor["first_observed_utc_ms"]),
        "exit_usable_quote_utc_ms":cutoff,
        "clock":clock,
        "entry_spot_cost_usdt":str(spot_entry),
        "entry_future_short_value_usdt":str(future_entry),
        "exit_spot_sale_bid_depth_usdt":str(spot_sale),
        "exit_future_cover_ask_depth_usdt":str(future_buyback),
        "gross_before_fees_and_funding_usdt":str(gross),
        "four_taker_fees_usdt":str(fees),
        "verified_settlement_timestamps_utc_ms":list(unique.keys()),
        "post_signal_settled_rate_sum":str(total_rate),
        "post_signal_settled_points":len(unique),
        "pre_signal_history_excluded":before_signal,
        "post_exit_history_excluded":after_exit,
        "funding_usdt_using_frozen_entry_notional_PROXY":str(funding_proxy),
        "closed_what_if_net_usdt_PROXY":str(mark),
        "owner_funding_received_usdt":"0",
        "real_orders":0,"paper_fills":0,
        "virtual_cash_rub_unchanged":50000,
        "limitations":[
            "Historical entry never executed; this is not a paper fill",
            "Funding paid on live mark notional; frozen entry proxy is approximate",
            "No hedge fill, fees, margin, client eligibility or RUB conversion verified",
            "Full roundtrip execution and latency are not proven"
        ]
    }


def settled_since_signal(anchor, funding_history, asof_ms):
    """Independent official past settlements, never account receipts."""
    import funding_gate as g
    earliest=int(anchor["first_observed_utc_ms"])
    found={}
    for row in funding_history:
        try:
            stamp=int(row["t"])*1000
            rate=g.D(row["r"])
            if (earliest<stamp<=int(asof_ms) and
                    abs(rate)<=Decimal(".1")):
                found.setdefault(stamp,rate)
        except (KeyError,ValueError,TypeError):
            continue
    return [{"settled_utc_ms":ms,"realized_rate":str(found[ms])}
            for ms in sorted(found)]


def mark_replay_from_candles(anchor, realized, rows):
    """Causal 1-minute PRE-settlement mark-price estimate, not actual fills.

    The candle starting 60 seconds before funding timestamp is the latest
    complete minute with no future data. OHLC range is a sensitivity bound,
    not a guaranteed settlement-price or historical execution bound.
    """
    import funding_gate as g
    by_start={}
    for c in rows:
        try:
            start=int(c["t"])
            op,high,low,close=(g.D(c[k]) for k in ("o","h","l","c"))
            if (min(op,high,low,close)<=0 or
                    high<max(op,close) or low>min(op,close)):
                continue
            by_start.setdefault(start,(op,high,low,close))
        except (KeyError,ValueError,TypeError):
            continue
    base=g.D(anchor["qty_base"])
    events=[]
    unavailable=[]
    total_mid=Decimal(0)
    total_low=Decimal(0)
    total_high=Decimal(0)
    for event in realized:
        ms=int(event["settled_utc_ms"])
        rate=g.D(event["realized_rate"])
        t=ms//1000-60
        candle=by_start.get(t)
        if candle is None:
            unavailable.append(ms)
            continue
        op,high,low,close=candle
        mid=base*close*rate
        lo=base*(low if rate>=0 else high)*rate
        hi=base*(high if rate>=0 else low)*rate
        total_mid+=mid; total_low+=lo; total_high+=hi
        events.append({"settled_utc_ms":ms,"price_candle_start_utc_s":t,
                       "minute_mark_close_usdt":str(close),
                       "minute_mark_low_usdt":str(low),
                       "minute_mark_high_usdt":str(high),
                       "realized_rate":str(rate),
                       "funding_mid_proxy_usdt":str(mid),
                       "funding_min_proxy_usdt":str(lo),
                       "funding_max_proxy_usdt":str(hi)})
    return {
        "status":("COMPLETE_1M_PRE_SETTLEMENT_MARK_PROXY" if not unavailable
                  else "INCOMPLETE_CANDLE_DATA_NO_ECONOMIC_PROMOTION"),
        "settled_total":len(realized),"settlements_with_mark_prices":len(events),
        "settlements_missing_mark_price":unavailable,
        "events":events,
        "total_funding_mark_close_proxy_usdt":str(total_mid),
        "total_funding_minute_low_bound_proxy_usdt":str(total_low),
        "total_funding_minute_high_bound_proxy_usdt":str(total_high),
        "not_actual_funding_receipts":True,
        "mark_close_not_exact_settlement_price":True
    }


def fetch_mark_candles(realized):
    """Public only. Frozen event times, small independently hashed mark windows."""
    import funding_gate as g
    if not realized:
        return {"status":"NO_SETTLEMENTS","candles":[],"hashes":[]}
    if len(realized)>24:
        return {"status":"SETTLEMENT_WINDOW_TOO_LARGE","candles":[],"hashes":[]}
    secs=sorted(int(x["settled_utc_ms"])//1000 for x in realized)
    start=secs[0]-120
    stop=secs[-1]+60
    queries=[]
    while start<stop:
        end=min(start+11*3600,stop)
        queries.append(g.ROOT+"/futures/usdt/candlesticks?contract=mark_"+PAIR+
            "&from="+str(start)+"&to="+str(end)+"&interval=1m")
        start=end  # overlap a minute rather than leaving an unobserved gap
    with ThreadPoolExecutor(max_workers=4) as ex:
        tasks=[ex.submit(g.public_json,q) for q in queries]
        replies=[p.result() for p in tasks]
    if not all(isinstance(x[0],list) for x in replies):
        return {"status":"BAD_MARK_CANDLE_RESPONSE","candles":[],"hashes":[]}
    return {"status":"PUBLIC_MARK_CANDLES_LOADED",
            "candles":[x for reply,_,_ in replies for x in reply],
            "hashes":[sh for _,_,sh in replies]}


def contract_check(anchor, contract):
    import funding_gate as g
    try:
        if (contract.get("name")!=anchor["pair"] or
                contract.get("type")!="direct" or
                contract.get("status","trading")!="trading" or
                contract.get("in_delisting") is not False or
                g.D(contract.get("quanto_multiplier","0"))!=g.D(anchor["contract_multiplier"])):
            return "CONTRACT_IDENTITY_CHANGED_OR_NOT_TRADING"
    except (ValueError,TypeError):
        return "BAD_CONTRACT_RESPONSE"
    return None


def observe():
    import funding_gate as g
    anchor=json.loads(ANCHOR_FILE.read_text(encoding="utf-8"))
    root=g.ROOT
    requests={
        "spot":root+"/spot/order_book?currency_pair="+PAIR+"&limit=10&with_id=true",
        "perp":root+"/futures/usdt/order_book?contract="+PAIR+"&limit=10&with_id=true",
        "paid":root+"/futures/usdt/funding_rate?contract="+PAIR+"&limit=100",
        "contract":root+"/futures/usdt/contracts/"+PAIR
    }
    with ThreadPoolExecutor(max_workers=4) as ex:
        tasks={k:ex.submit(g.public_json,u) for k,u in requests.items()}
        d={k:f.result() for k,f in tasks.items()}
    quote_time=int(time.time()*1000)
    if not isinstance(d["paid"][0],list) or not isinstance(d["contract"][0],dict):
        return {"status":"BAD_PUBLIC_RESPONSE","real_orders":0,"paper_fills":0}
    receipts=settled_since_signal(anchor,d["paid"][0],quote_time)
    first_expected_ms=int(anchor["initial_next_payment_utc_s"])*1000
    first_expected=[x for x in receipts if x["settled_utc_ms"]==first_expected_ms]
    try:
        mark_source=fetch_mark_candles(receipts)
        if mark_source["status"]=="PUBLIC_MARK_CANDLES_LOADED":
            mark_model=mark_replay_from_candles(anchor,receipts,mark_source["candles"])
        else:
            mark_model={"status":mark_source["status"]}
    except (OSError,ValueError,KeyError,TypeError,TimeoutError) as exc:
        mark_source={"status":"MARK_CANDLE_DATA_BLOCKED","hashes":[]}
        mark_model={"status":"MARK_CANDLE_DATA_BLOCKED","error_class":type(exc).__name__}
    reason=contract_check(anchor,d["contract"][0])
    if reason:
        result={"status":reason}
    else:
        result=evaluate(anchor,d["spot"][0],d["perp"][0],d["paid"][0],
                        d["spot"][1],d["perp"][1],quote_time)
    if (mark_model.get("status")=="COMPLETE_1M_PRE_SETTLEMENT_MARK_PROXY" and
            result.get("status")=="CAUSAL_UNFILLED_COUNTERFACTUAL_MARK"):
        lower=g.D(mark_model["total_funding_minute_low_bound_proxy_usdt"])
        upper=g.D(mark_model["total_funding_minute_high_bound_proxy_usdt"])
        estimated=g.D(mark_model["total_funding_mark_close_proxy_usdt"])
        gross=g.D(result["gross_before_fees_and_funding_usdt"])
        fees=g.D(result["four_taker_fees_usdt"])
        mark_model["revalued_closed_net_mark_price_proxy_usdt"]=str(gross-fees+estimated)
        mark_model["revalued_closed_net_lower_minute_range_proxy_usdt"]=str(gross-fees+lower)
        mark_model["revalued_closed_net_upper_minute_range_proxy_usdt"]=str(gross-fees+upper)
        mark_model["all_legs_actually_filled"]=False
    return {"schema":"NORM03B_M2_IRYS_FIXED_FORWARD_V1",
            "source_run_id":anchor["source_run_id"],
            "anchor_source_zip_sha256":anchor["source_zip_sha256"],
            "asof_utc_ms":quote_time,
            "source_sha256":{k:v[2] for k,v in d.items()},
            "realized_past_settlements_after_signal":receipts,
            "settlements_after_frozen_signal_count":len(receipts),
            "original_expected_settlement_utc_ms":first_expected_ms,
            "original_expected_settlement_observed":len(first_expected)>0,
            "original_expected_settlement_realized_rate":(
                first_expected[0]["realized_rate"] if first_expected else None),
            "actual_owner_funding_received":"0",
            "mark_price_replay":mark_model,
            "mark_price_public_source_sha256":mark_source.get("hashes",[]),
            "calculation":result,"paper_fills":0,"real_orders":0,
            "virtual_rub_equity":50000}


if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",required=True,type=Path)
    args=ap.parse_args()
    try:
        result=observe()
    except (OSError,ValueError,TypeError,KeyError) as exc:
        result={"schema":"NORM03B_M2_IRYS_FIXED_FORWARD_V1",
                "status":"DATA_BLOCKED","error_class":type(exc).__name__,
                "real_orders":0,"paper_fills":0,"virtual_rub_equity":50000}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":result.get("calculation",{}).get("status",result.get("status")),
        "counterfactual_usdt":result.get("calculation",{}).get("closed_what_if_net_usdt_PROXY"),
        "paper_fills":0,"real_orders":0}))
