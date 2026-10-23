#!/usr/bin/env python3
"""NORM 03B M9: inverse USD-settled-in-coin vs linear USDT perpetual carry.

Independent public OKX research, never orders or portfolio fills. The two
legs have DIFFERENT collateral/settlement currencies, imperfect delta
hedging and USD/USDT basis; therefore this is a relative-value screen,
NOT risk-free arbitrage, even when historical funding exceeds costs.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, InvalidOperation
from pathlib import Path
from urllib.request import Request, urlopen
import argparse
import hashlib
import json
import time

ROOT="https://www.okx.com/api/v5"
INST=ROOT+"/public/instruments?instType=SWAP"
WATCH=15
SELECT=7
TARGET_USD=(Decimal("100"),Decimal("250"))
MAX_QUOTE_SKEW_MS=1500
MAX_AGE_MS=5000
FEE_TAKER=Decimal("0.0005")  # 0.05% per side, unverified personal tier
MAX_DELTA_MISMATCH=Decimal("0.0075")
HISTORY=30

def D(x):
    d=Decimal(str(x))
    if not d.is_finite():raise ValueError("nonfinite number")
    return d

def clock_ms():return time.time_ns()//1_000_000

def url(kind,asset="",quote="USD"):
    if asset and (not asset.isalnum() or len(asset)>12):raise ValueError("unknown asset")
    if quote not in ("USD","USDT"):raise ValueError("not approved quote")
    symbol=asset+"-"+quote+"-SWAP"
    if kind=="instruments" and not asset:return INST
    if not asset:raise ValueError("missing asset")
    if kind=="books":return ROOT+"/market/books?instId="+symbol+"&sz=10"
    if kind=="funding":return ROOT+"/public/funding-rate?instId="+symbol
    if kind=="history":return ROOT+"/public/funding-rate-history?instId="+symbol+"&limit=30"
    raise ValueError("unapproved market URL kind")

def get(url_str):
    if not url_str.startswith(ROOT+"/") or "/trade/" in url_str:
        raise ValueError("only public data allowed")
    req=Request(url_str,method="GET",headers={"Accept":"application/json","User-Agent":"NORM03B-INVERSE-LINEAR/1"})
    with urlopen(req,timeout=9) as res:
        if res.status!=200:raise ValueError("http response")
        blob=res.read(3000001)
        recv=clock_ms()
    if len(blob)>3000000:raise ValueError("too many response bytes")
    j=json.loads(blob)
    if j.get("code")!="0" or not isinstance(j.get("data"),list):
        raise ValueError("public provider returned error")
    return j["data"],recv,hashlib.sha256(blob).hexdigest()

def live_contracts(rows):
    inv,lin={},{}
    for x in rows:
        try:
            if x.get("instType")!="SWAP" or x.get("state")!="live" or x.get("ctMult")!="1":
                continue
            symbol=x["instId"]
            if symbol.endswith("-USD-SWAP") and x["ctType"]=="inverse" and x["ctValCcy"]=="USD":
                a=symbol[:-9]
                inv[a]=x
            if symbol.endswith("-USDT-SWAP") and x["ctType"]=="linear":
                a=symbol[:-10]
                if x["ctValCcy"]!=a:continue
                lin[a]=x
        except (KeyError,TypeError):continue
    out={}
    for a in inv.keys() & lin.keys():
        try:
            fi,fl=inv[a],lin[a]
            if D(fi["ctVal"])<=0 or D(fl["ctVal"])<=0:continue
            if D(fi["lotSz"])<=0 or D(fl["lotSz"])<=0:continue
            if D(fi["minSz"])<=0 or D(fl["minSz"])<=0:continue
            out[a]={"inverse":fi,"linear":fl}
        except (KeyError,InvalidOperation,ValueError):continue
    return out

def settle_rate(rows):
    if len(rows)!=1:raise ValueError("funding response is not singleton")
    x=rows[0]
    return {"rate":D(x["fundingRate"]),"time_ms":int(x["fundingTime"])}

def historical_diff(inv_history,lin_history,side="SHORT_INVERSE"):
    def parse(rows):
        r={}
        for x in rows:
            try:
                ts=int(x["fundingTime"])
                rate=D(x["realizedRate"])
                if abs(rate)>.1:continue
                r[ts]=rate
            except (ValueError,KeyError,TypeError,InvalidOperation):
                continue
        return r
    a,b=parse(inv_history),parse(lin_history)
    when=sorted(a.keys() & b.keys(),reverse=True)[:HISTORY]
    if side not in ("SHORT_INVERSE","LONG_INVERSE"):
        raise ValueError("not a hedged direction")
    sign=Decimal("1") if side=="SHORT_INVERSE" else Decimal("-1")
    diffs=[(a[t]-b[t])*sign for t in when]
    return {
        "time_ms":when,
        "last3_sum_bp":str(sum(diffs[:3],Decimal(0))*10000),
        "last9_sum_bp":str(sum(diffs[:9],Decimal(0))*10000),
        "last30_sum_bp":str(sum(diffs,Decimal(0))*10000),
        "last30_count":len(when),
        "last30_positive_count":sum(d>0 for d in diffs),
        "last30_diff_rates":[str(x) for x in diffs],
        "older_observations_not_owner_pnl":True
    }

def parse_book(rows,received):
    if len(rows)!=1:raise ValueError("book is not singleton")
    x=rows[0]
    timestamp=int(x["ts"]);age=received-timestamp
    if not -1100<=age<=MAX_AGE_MS:raise ValueError("stale or future book")
    def level(side):
        z=[]
        for v in x[side][:10]:
            p,n=D(v[0]),D(v[1])
            if p<=0 or n<=0:raise ValueError("bad level")
            z.append((p,n))
        if not z:raise ValueError("empty book")
        if z!=sorted(z,key=lambda y:y[0],reverse=(side=="bids")):
            raise ValueError("levels unsorted")
        return z
    return {"bids":level("bids"),"asks":level("asks"),
            "exchange_ms":timestamp,"received_ms":received}

def sweep(book,side,num_contracts):
    if side not in ("bids","asks"):raise ValueError("not a side")
    left=num_contracts
    cost=Decimal(0)
    for price,qty in book[side]:
        used=min(left,qty)
        cost+=used*price
        left-=used
        if left<=0:return cost/num_contracts
    return None

def rounded(raw,step):
    if step<=0:raise ValueError("bad step")
    return (raw/step).to_integral_value(rounding=ROUND_HALF_UP)*step

def economics(asset,ins,lin_book,inv_book,inv_rate,lin_rate,inv_hist,lin_hist,desired_usd):
    if abs(lin_book["exchange_ms"]-inv_book["exchange_ms"])>MAX_QUOTE_SKEW_MS:
        return {"status":"BOOKS_DESYNCHRONIZED","asset":asset}
    f_inv,f_lin=ins["inverse"],ins["linear"]
    face=D(f_inv["ctVal"])*D(f_inv["ctMult"])
    cval=D(f_lin["ctVal"])*D(f_lin["ctMult"])
    invlot=D(f_inv["lotSz"]);linlot=D(f_lin["lotSz"])
    inv_count=(desired_usd/face/invlot).to_integral_value(rounding=ROUND_FLOOR)*invlot
    if inv_count<D(f_inv["minSz"]) or inv_count<=0:
        return {"status":"MIN_INVERSE_CONTRACT_NOT_MET","asset":asset}
    inverse_usd=inv_count*face
    direction="SHORT_INVERSE" if inv_rate["rate"]>=lin_rate["rate"] else "LONG_INVERSE"
    iv_entry_side="bids" if direction=="SHORT_INVERSE" else "asks"
    li_entry_side="asks" if direction=="SHORT_INVERSE" else "bids"
    iv_exit_side="asks" if direction=="SHORT_INVERSE" else "bids"
    li_exit_side="bids" if direction=="SHORT_INVERSE" else "asks"
    inv_entry=sweep(inv_book,iv_entry_side,inv_count)
    if inv_entry is None:return {"status":"INVERSE_BOOK_NO_DEPTH","asset":asset}
    base_qty=inverse_usd/inv_entry
    lin_count=rounded(base_qty/cval,linlot)
    if lin_count<D(f_lin["minSz"]):
        return {"status":"MIN_LINEAR_CONTRACT_NOT_MET","asset":asset}
    lin_qty=lin_count*cval
    mismatch=abs(lin_qty-base_qty)/base_qty
    if mismatch>MAX_DELTA_MISMATCH:
        return {"status":"DELTA_MISMATCH_TOO_HIGH","asset":asset,
                "delta_mismatch_pct":str(mismatch*100)}
    inv_exit=sweep(inv_book,iv_exit_side,inv_count)
    lin_entry=sweep(lin_book,li_entry_side,lin_count)
    lin_exit=sweep(lin_book,li_exit_side,lin_count)
    if any(x is None for x in (inv_exit,lin_entry,lin_exit)):
        return {"status":"INSUFFICIENT_BOOK_DEPTH","asset":asset}
    # For inverse USD-faced contracts, realized PnL is paid in BASE TOKEN.
    # Convert at linear exit reference price as an optimistic USDT proxy.
    if direction=="SHORT_INVERSE":
        pnl_inv_base=inverse_usd*(1/inv_exit-1/inv_entry)
        pnl_lin_usdt=lin_qty*(lin_exit-lin_entry)
    else:
        pnl_inv_base=inverse_usd*(1/inv_entry-1/inv_exit)
        pnl_lin_usdt=lin_qty*(lin_entry-lin_exit)
    pnl_inv_usdt_proxy=pnl_inv_base*lin_exit
    four_taker_fees_proxy=FEE_TAKER*(inverse_usd*2+lin_qty*(lin_entry+lin_exit))
    net_exit_now=pnl_inv_usdt_proxy+pnl_lin_usdt-four_taker_fees_proxy
    spread_rate=(inv_rate["rate"]-lin_rate["rate"])*(1 if direction=="SHORT_INVERSE" else -1)
    carry_one=inverse_usd*spread_rate
    hist=historical_diff(inv_hist,lin_hist,direction)
    sum_last30=D(hist["last30_sum_bp"])/D(10000)
    hist_usdt_proxy=inverse_usd*sum_last30
    breakeven_n=(-net_exit_now/carry_one) if carry_one>0 else None
    # BASIS and USD/USDT pegs are NOT locked. This is not a clean FX hedge.
    return {"status":"HEDGED_FUNDING_BASIS_SCREEN_NO_FILL",
            "asset":asset,"direction":direction,"inverse_contracts":str(inv_count),
            "linear_contracts":str(lin_count),"inverse_face_usd":str(inverse_usd),
            "linear_qty_base":str(lin_qty),"inverse_base_exposure":str(base_qty),
            "delta_mismatch_pct":str(mismatch*100),
            "inverse_entry_usd":str(inv_entry),"inverse_exit_usd":str(inv_exit),
            "linear_entry_usdt":str(lin_entry),"linear_exit_usdt":str(lin_exit),
            "gross_immediate_exit_usdt_proxy":str(pnl_inv_usdt_proxy+pnl_lin_usdt),
            "four_taker_fees_usdt_proxy":str(four_taker_fees_proxy),
            "net_immediate_exit_usdt_proxy":str(net_exit_now),
            "next_indicative_carry_usdt_proxy":str(carry_one),
            "one_period_breakeven":carry_one+net_exit_now>0,
            "same_indicative_funding_periods_to_breakeven":str(breakeven_n) if breakeven_n is not None else None,
            "historical":hist,
            "historical_30_funding_usdt_proxy":str(hist_usdt_proxy),
            "SCENARIO_only_if_next_30_repeat_historical_funding":str(net_exit_now+hist_usdt_proxy),
            "linear_swap_settlement":f_lin.get("settleCcy"),
            "inverse_swap_settlement":f_inv.get("settleCcy"),
            "cross_currency_peg_risk":True,
            "inverse_base_coin_collateral_required":True,
            "liquidation_risk_and_margin_unverified":True,
            "symbol_underlying_not_contractually_verified":True,
            "access_rights_not_verified":True,
            "futures_fee_user_tier_verified":False,
            "fees":str(FEE_TAKER),
            "book_skew_ms":abs(lin_book["exchange_ms"]-inv_book["exchange_ms"]),
            "time_ms":[lin_book["exchange_ms"],inv_book["exchange_ms"]],
            "real_orders":0,"paper_fills":0}

def screen():
    items,_,digest=get(url("instruments"))
    contracts=live_contracts(items)
    # Current rates can be discovered cheaply before the orderbook selection.
    funding={}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures={pool.submit(get,url("funding",asset,q)):(asset,q)
                 for asset in contracts for q in ("USD","USDT")}
        for f in as_completed(futures):
            a,q=futures[f]
            try:
                rows,rec,sha=f.result()
                funding[(a,q)]=settle_rate(rows)
            except (OSError,ValueError,KeyError,TypeError,InvalidOperation):
                continue
    ranking=[]
    for a in contracts:
        if (a,"USD") not in funding or (a,"USDT") not in funding:continue
        x,y=funding[(a,"USD")],funding[(a,"USDT")]
        if abs(x["time_ms"]-y["time_ms"])>5000:continue
        ranking.append((abs(x["rate"]-y["rate"]),a))
    ranking.sort(reverse=True)
    chosen=[a for _,a in ranking[:SELECT]]
    results=[]
    for a in chosen:
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                future={ (kind,q):pool.submit(get,url(kind,a,q))
                    for kind in ("books","history") for q in ("USD","USDT")}
                grabbed={key:f.result() for key,f in future.items()}
            invb=parse_book(grabbed[("books","USD")][0],grabbed[("books","USD")][1])
            linb=parse_book(grabbed[("books","USDT")][0],grabbed[("books","USDT")][1])
            econ=[economics(a,contracts[a],linb,invb,funding[(a,"USD")],
                      funding[(a,"USDT")],grabbed[("history","USD")][0],
                      grabbed[("history","USDT")][0],target) for target in TARGET_USD]
            results.append({"asset":a,"funding_diff_current_bp":str((funding[(a,"USD")]["rate"]-
                             funding[(a,"USDT")]["rate"])*10000),
                "current_funding_time_ms":funding[(a,"USD")]["time_ms"],
                "contract_details":{"inverse":{k:contracts[a]["inverse"].get(k) for k in
                    ("instId","ctType","ctVal","ctValCcy","ctMult","minSz","lotSz","settleCcy")},
                    "linear":{k:contracts[a]["linear"].get(k) for k in
                    ("instId","ctType","ctVal","ctValCcy","ctMult","minSz","lotSz","settleCcy")}},
                "screen":econ,
                "source_hashes":{k[0]+"_"+k[1]:v[2] for k,v in grabbed.items()}})
        except (OSError,ValueError,KeyError,TypeError,InvalidOperation):
            results.append({"asset":a,"status":"DATA_FAILED_NOT_A_NO_OPPORTUNITY"})
    usable=[o for x in results for o in x.get("screen",[]) if o.get("status")=="HEDGED_FUNDING_BASIS_SCREEN_NO_FILL"]
    historical_counterfactuals=sum(D(x["SCENARIO_only_if_next_30_repeat_historical_funding"])>0 for x in usable)
    return {"schema":"NORM03B_M9_OKX_INVERSE_LINEAR_HEDGED_FUNDING_V1",
        "time_utc_ms":clock_ms(),"mode":"READ_ONLY",
        "funding_pair_count":len(ranking),"selected_assets":chosen,
        "screened_complete_hedges":len(usable),
        "historic_30_repeat_scenario_positive":historical_counterfactuals,
        "confirmed_future_realized_pnl_cycles":0,
        "instruments_source_sha256":digest,"results":results,
        "limitations":["historic 30 payments predate entry, never credit as NORM profits",
            "inverse USD derivative collateral settles in base coin while linear settles USDT",
            "USD-USDT peg basis, inverse convexity and liquidation are residual exposures",
            "four taker fees are scenarios; market route and owner access unverified",
            "no future funding rate guaranteed or orderbook depth proven at future close",
            "RUB/USDT conversion unavailable; 50k RUB account must remain unchanged"],
        "cash_rub":50000,"equity_rub":50000,"paper_fills":0,"real_orders":0}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    data=screen()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:data[k] for k in ("funding_pair_count","selected_assets",
        "screened_complete_hedges","historic_30_repeat_scenario_positive",
        "confirmed_future_realized_pnl_cycles","paper_fills","real_orders")}))
    return 0

if __name__=="__main__":raise SystemExit(main())
