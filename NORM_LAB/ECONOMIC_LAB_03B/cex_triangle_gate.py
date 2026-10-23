#!/usr/bin/env python3
"""NORM 03B M7 — OKX same-exchange triangular arbitrage, public-only shadow.

Cycle USDT -> token A -> token B -> USDT, three real spot instruments.
Uses live instrument definitions, bid/ask, size, minimum and lot rounding.
Outputs bid/ask screens and causally later independent quote witnesses;
never sends any order, pretends a fill, or mutates RUB virtual cash.
Three independent exchange orders are NOT an atomic transaction.
"""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import time

ROOT = "https://www.okx.com/api/v5"
TICKERS = ROOT + "/market/tickers?instType=SPOT"
INSTRUMENTS = ROOT + "/public/instruments?instType=SPOT"
START_CURRENCY = "USDT"
AMOUNTS = ("100", "250")
TAKER_FEE = Decimal("0.001")  # 0.10% default-cost stress; client/account not known
# Conditional scenario only: some jurisdictions have 0% DAI/PYUSD/USDC USDT fees;
# OKX announced 0.04% taker on USDG/RLUSD-USDT from 22 June 2026.
# Does NOT imply availability for a resident of Russia or owner's account.
REGIONAL_PROMO_SCENARIO={
    "DAI-USDT": Decimal("0"),
    "PYUSD-USDT": Decimal("0"),
    "USDC-USDT": Decimal("0"),
    "USDG-USDT": Decimal("0.0004"),
    "RLUSD-USDT": Decimal("0.0004")
}
CLOCK_MAX_AGE_MS = 5000
CLOCK_MAX_FUTURE_MS = 1100
MAX_TRIANGLE_SKEW_MS = 1500
MAX_ROUNDS = 12

def num(x):
    value=Decimal(str(x))
    if not value.is_finite(): raise ValueError("NONFINITE")
    return value

def now_ms():
    return time.time_ns()//1_000_000

def fetch(url):
    if url not in (TICKERS, INSTRUMENTS):raise ValueError("UNLISTED_ENDPOINT")
    request=Request(url,method="GET",headers={
        "Accept":"application/json", "User-Agent":"NORM03B_TRI_ARBITRAGE_READONLY/1"})
    with urlopen(request,timeout=9) as r:
        if r.status!=200:raise ValueError("HTTP_FAILED")
        payload=r.read(2_600_001);recv=now_ms()
    if len(payload)>2_600_000:raise ValueError("TOO_LARGE")
    item=json.loads(payload)
    if item.get("code")!="0" or not isinstance(item.get("data"),list):
        raise ValueError("EXCHANGE_ERROR")
    return item["data"],recv,hashlib.sha256(payload).hexdigest()

def quantized(size,step):
    if step<=0:raise ValueError("BAD_LOT")
    return (size/step).to_integral_value(rounding=ROUND_DOWN)*step

def instruments_map(rows):
    result={}
    for x in rows:
        try:
            if x.get("instType")!="SPOT" or x.get("state")!="live":
                continue
            k=x["instId"];base=x["baseCcy"];quote=x["quoteCcy"]
            mini,lot=num(x["minSz"]),num(x["lotSz"])
            if not base or not quote or base==quote or mini<=0 or lot<=0:
                continue
            if not isinstance(k,str) or k!=base+"-"+quote:
                continue
            result[k]={"symbol":k,"base":base,"quote":quote,
                       "min":mini,"lot":lot}
        except (KeyError,InvalidOperation,TypeError,ValueError):
            continue
    return result

def quote_map(definitions,rows,received):
    out={}
    bad=0
    for x in rows:
        try:
            symbol=x["instId"]
            if symbol not in definitions:continue
            t=int(x["ts"])
            age=received-t
            if not -CLOCK_MAX_FUTURE_MS<=age<=CLOCK_MAX_AGE_MS:
                bad+=1;continue
            ask,bid=num(x["askPx"]),num(x["bidPx"])
            aq,bq=num(x["askSz"]),num(x["bidSz"])
            if min(ask,bid,aq,bq)<=0 or ask<=bid:bad+=1;continue
            out[symbol]={**definitions[symbol],"ask":ask,"bid":bid,
                         "ask_sz":aq,"bid_sz":bq,"exchange_ms":t}
        except (KeyError,InvalidOperation,TypeError,ValueError,OverflowError):
            bad+=1
    return out,bad

def triangles(markets,start=START_CURRENCY):
    edges=defaultdict(dict)
    for m in markets.values():
        a,b=m["base"],m["quote"]
        edges[a][b]=(m["symbol"],"SELL")
        edges[b][a]=(m["symbol"],"BUY")
    result=[]
    for a in edges[start]:
        if a==start:continue
        for b in edges[a]:
            if b in (start,a) or start not in edges[b]:
                continue
            result.append((
                (start,a,*edges[start][a]),
                (a,b,*edges[a][b]),
                (b,start,*edges[b][start])))
    return result

def hop(m,source,amount,side,fee=TAKER_FEE):
    if amount<=0 or fee<0 or fee>=1:return None
    if side=="SELL":
        if m["base"]!=source:return None
        quantity=quantized(amount,m["lot"])
        if quantity<m["min"] or quantity>m["bid_sz"]:return None
        return quantity*m["bid"]*(1-fee)
    if side=="BUY":
        if m["quote"]!=source:return None
        quantity=quantized(amount/m["ask"],m["lot"])
        if quantity<m["min"] or quantity>m["ask_sz"]:return None
        return quantity*(1-fee)
    raise ValueError("INVALID_HOP")

def evaluate(path,markets,notional,fee_by_symbol=None):
    current=num(notional)
    stamps=[]
    markets_in_order=[]
    for src,dst,symbol,side in path:
        m=markets.get(symbol)
        if m is None:return None
        assumed_fee=fee_by_symbol.get(symbol,TAKER_FEE) if fee_by_symbol is not None else TAKER_FEE
        amount=hop(m,src,current,side,fee=assumed_fee)
        if amount is None or amount<=0:return None
        current=amount
        stamps.append(m["exchange_ms"])
        markets_in_order.append({"symbol":symbol,"side":side,
                                 "source":src,"destination":dst,
                                 "exchange_ms":m["exchange_ms"],
                                 "fee_assumed":str(assumed_fee)})
    if max(stamps)-min(stamps)>MAX_TRIANGLE_SKEW_MS:return None
    return {"starting_usdt":str(notional),
            "end_usdt_conservative":str(current),
            "net_usdt_conservative":str(current-num(notional)),
            "net_bp_conservative":str((current/num(notional)-1)*10000),
            "hops":markets_in_order,
            "max_snapshot_skew_ms":max(stamps)-min(stamps),
            "status":"UNEXECUTED_NONATOMIC_QUOTE_SCREEN",
            "requires_three_fill_confirmation":True,
            "real_orders":0,"paper_fills":0}


def uniform_fee_breakeven(path, markets, notional):
    """The greatest *equal taker fee on each leg* allowing >0 final USDT.

    This is a mathematical counterfactual, not a personalized fee tier or
    executable IOC. Exact lot-rounding and top-level size are re-evaluated.
    """
    fees0={hop[2]:Decimal("0") for hop in path}
    zerocost=evaluate(path,markets,notional,fees0)
    if zerocost is None:
        return None
    pnl0=num(zerocost["net_usdt_conservative"])
    if pnl0<=0:
        return None
    low=Decimal(0)
    high=Decimal("0.001")
    for _ in range(24):
        mid=(low+high)/2
        result=evaluate(path,markets,notional,{h[2]:mid for h in path})
        if result is not None and num(result["net_usdt_conservative"])>0:
            low=mid
        else:
            high=mid
    return {
        "route": [(h[2],h[3]) for h in path],
        "notional_usdt":str(notional),
        "zero_fee_gross_net_usdt_after_lot_round":str(pnl0),
        "uniform_fee_break_even_bp_per_leg":str(low*10000),
        "default_fee_bp_per_leg":str(TAKER_FEE*10000),
        "owner_fee_verified":False,
        "status":"THEORETICAL_FEE_FRONTIER_NO_FILL"
    }

def one_scan():
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_def=pool.submit(fetch,INSTRUMENTS)
        future_ticks=pool.submit(fetch,TICKERS)
        defs,_,def_digest=future_def.result()
        ticks,recv,tick_digest=future_ticks.result()
    definitions=instruments_map(defs)
    markets,bad=quote_map(definitions,ticks,recv)
    paths=triangles(markets)
    valid=[]
    regional=[]
    fee_frontier=[]
    for path in paths:
        for amount in AMOUNTS:
            r=evaluate(path,markets,amount)
            if r is not None:valid.append(r)
            zero_all=uniform_fee_breakeven(path,markets,amount)
            if zero_all is not None:
                fee_frontier.append(zero_all)
            alternative=evaluate(path,markets,amount,REGIONAL_PROMO_SCENARIO)
            if alternative is not None and num(alternative["net_usdt_conservative"])>0:
                alternative["fee_scenario"]="JURISDICTION_CONDITIONAL_NOT_OWNER_VERIFIED"
                regional.append(alternative)
    valid.sort(key=lambda r:num(r["net_usdt_conservative"]),reverse=True)
    regional.sort(key=lambda r:num(r["net_usdt_conservative"]),reverse=True)
    fee_frontier.sort(key=lambda r:num(r["uniform_fee_break_even_bp_per_leg"]),reverse=True)
    return {
        "received_at_ms":recv,"listed_live_instruments":len(definitions),
        "fresh_usable_ticker_markets":len(markets),
        "stale_or_bad_market_rows":bad,
        "possible_triangle_paths":len(paths),
        "fully_priced_roundtrips":len(valid),
        "positive_after_3_taker_fees_and_lot_rounding":
            sum(num(x["net_usdt_conservative"])>0 for x in valid),
        "top_roundtrips":valid[:7],
        "regional_promo_positive_quote_screens":len(regional),
        "regional_promo_best_quote_screens":regional[:5],
        "regional_promo_owner_eligible":False,
        "positive_without_taker_fees_after_bid_ask_and_lots":len(fee_frontier),
        "best_uniform_fee_frontier":fee_frontier[:6],
        "fee_frontier_is_not_a_fill":True,
        "digest_instruments":def_digest,"digest_tickers":tick_digest},valid

def track(seconds,interval):
    start=time.monotonic()
    overviews=[];pending={};signaled=set();later=[];rejected=0
    while len(overviews)<MAX_ROUNDS and time.monotonic()-start<seconds:
        try:
            view,records=one_scan()
        except (OSError,ValueError,KeyError,TypeError,IndexError,TimeoutError) as err:
            overviews.append({"received_at_ms":now_ms(),"source_error":type(err).__name__})
            records=[]
            view=None
        else:
            overviews.append(view)
        lookup={}
        for r in records:
            k=tuple((h["symbol"],h["side"]) for h in r["hops"])+(r["starting_usdt"],)
            lookup[k]=r
        for k,first in list(pending.items()):
            nxt=lookup.get(k)
            if nxt is not None and all(
                  n["exchange_ms"]>p["exchange_ms"]
                  for n,p in zip(nxt["hops"],first["hops"])):
                if num(nxt["net_usdt_conservative"])>0:
                    later.append({"first":first,"second":nxt,
                         "evidence":"TWO_PROSPECTIVE_QUOTES_NOT_3_FILLED_ORDERS"})
                else:rejected+=1
                del pending[k]
        for k,r in lookup.items():
            if num(r["net_usdt_conservative"])>0 and k not in pending:
                uniq=(k,tuple(h["exchange_ms"] for h in r["hops"]))
                if uniq not in signaled:
                    signaled.add(uniq);pending[k]=r
        elapsed=time.monotonic()-start
        if elapsed+interval>=seconds:break
        time.sleep(interval)
    return {
       "schema":"NORM03B_OKX_M7_SAME_VENUE_TRIANGLE_READONLY_V1",
       "mode":"READ_ONLY_NO_ORDERS","created_utc_ms":now_ms(),
       "initial_quote_positive_unique":len(signaled),
       "later_independent_positive_quote_witnesses":later[:10],
       "negative_or_gone_after_next_quotes":rejected,
       "pending_at_end":len(pending),"snapshots":overviews,
       "fees_each_leg":str(TAKER_FEE),
       "regional_promo_sensitivity_only":{k:str(v) for k,v in REGIONAL_PROMO_SCENARIO.items()},
       "regional_fee_owner_eligibility_verified":False,
       "not_atomic":True,"orderbook_depth_limited":"TICKER_BEST_BID_ASK_ONLY",
       "owner_exchange_access_verified":False,
       "rub_to_usdt_conversion_verified":False,
       "complete_filled_arbitrage_cycles":0,
       "rub_cash_unchanged":50000,"rub_equity_unchanged":50000,
       "paper_fills":0,"real_orders":0}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--seconds",type=int,default=44)
    ap.add_argument("--interval",type=int,default=8)
    ap.add_argument("--out",type=Path,required=True)
    ns=ap.parse_args()
    if not 10<=ns.seconds<=60 or not 3<=ns.interval<=20:
        ap.error("bounded collection")
    r=track(ns.seconds,ns.interval)
    ns.out.parent.mkdir(parents=True,exist_ok=True)
    ns.out.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "snapshots":len(r["snapshots"]),
        "most_valid_cycles":max((s.get("fully_priced_roundtrips",0) for s in r["snapshots"]),default=0),
        "all_snapshot_after_cost_signals":sum(s.get("positive_after_3_taker_fees_and_lot_rounding",0) for s in r["snapshots"]),
        "prospective_price_witnesses":len(r["later_independent_positive_quote_witnesses"]),
        "conditional_promo_screen_positive":sum(s.get("regional_promo_positive_quote_screens",0) for s in r["snapshots"]),
        "virtual_fills":r["paper_fills"],"real_orders":r["real_orders"]}))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
