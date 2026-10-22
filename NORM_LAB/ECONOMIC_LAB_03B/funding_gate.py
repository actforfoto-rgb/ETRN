#!/usr/bin/env python3
"""NORM 03B M2: hedged spot/perpetual funding economic gate (public data only).

Reads Gate current books, current funding rate and SETTLED historical funding rates.
Never sends orders. Historical receipts are a sensitivity measure, NOT our PnL.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import argparse
import hashlib
import json
import re
from pathlib import Path
import time
from urllib.request import Request, urlopen

ROOT="https://api.gateio.ws/api/v4"
PAIR_RE=re.compile(r"^[A-Z0-9]{2,25}_USDT$")
SPOT_TAKER=Decimal("0.001")  # Gate base-fee assumption, not client tariff
FUT_TAKER=Decimal("0.0005")
TARGET_USDT=Decimal("100")
MAX_BOOK_AGE_MS=15000
MAX_SKEW_MS=3000
MIN_RATE=Decimal("0.00020")
NUM_SELECTED=10
MIN_HISTORY=8

def D(v):
    p=Decimal(str(v))
    if not p.is_finite(): raise ValueError("non-finite")
    return p

def public_json(url):
    if not url.startswith(ROOT+"/"): raise ValueError("off-whitelist API")
    req=Request(url,headers={"User-Agent":"NORM03B-PUBLIC-M2/1","Accept":"application/json"},method="GET")
    with urlopen(req,timeout=9) as r:
        if r.status!=200: raise ValueError("HTTP_NOT_OK")
        b=r.read(2500001)
    if len(b)>2500000: raise ValueError("BOUNDED_INPUT_EXCEEDED")
    return json.loads(b),int(time.time()*1000),hashlib.sha256(b).hexdigest()

def shortlist(futures,spots):
    spot={r.get("currency_pair"):r for r in spots if isinstance(r,dict)}
    out=[]
    for r in futures:
        if not isinstance(r,dict): continue
        pair=r.get("contract","")
        if not isinstance(pair,str) or not PAIR_RE.fullmatch(pair) or pair not in spot: continue
        try:
            fund=D(r.get("funding_rate","0")); sa=D(spot[pair]["lowest_ask"])
            fb=D(r["highest_bid"]); mult=D(r.get("quanto_multiplier","0"))
            if min(sa,fb,mult)<=0 or fund<MIN_RATE: continue
            ratio=fb/sa
            if ratio<Decimal("0.95") or ratio>Decimal("1.05"): continue
            out.append({"pair":pair,"asset":pair[:-5],"funding_rate":fund,
                        "spot_ask":sa,"future_bid":fb,"multiplier":mult,
                        "rate_to_spread_score":fund+(ratio-1)})
        except (ValueError,KeyError,InvalidOperation,TypeError):continue
    # Rank by potential same-currency entry premium PLUS known next funding rate.
    out.sort(key=lambda r:r["rate_to_spread_score"],reverse=True)
    return out[:NUM_SELECTED]

def levels(book,future,side):
    data=book.get(side,[])
    try:
        if future:return [(D(x["p"]),D(x["s"])) for x in data[:10]]
        return [(D(x[0]),D(x[1])) for x in data[:10]]
    except (KeyError,TypeError,IndexError,ValueError,InvalidOperation):
        return []

def walk(levels,qty,unit=Decimal("1")):
    if qty<=0:return None
    left=qty;total=Decimal(0)
    for p,s in levels:
        if p<=0 or s<=0:return None
        used=min(left,s*unit);total+=used*p;left-=used
        if left<=0:return total
    return None

def book_ms(book,future):
    v=D(book.get("update",0))
    return int(v*1000) if future else int(v)

def valid_time(spot_book,fut_book,spot_receive,fut_receive):
    st=book_ms(spot_book,False)
    ft=book_ms(fut_book,True)
    ages=(spot_receive-st,fut_receive-ft)
    skew=abs(st-ft)
    # A quote cannot occur over one second in the future from client wall clock.
    return (all(-1000<=x<=MAX_BOOK_AGE_MS for x in ages)
            and skew<=MAX_SKEW_MS), {"book_age_ms":ages,"skew_ms":skew,"exchange_ms":[st,ft]}

def rates(records,when):
    x=[]
    for r in records:
        try:
            ts=int(r["t"])
            if ts>=when//1000:continue
            val=D(r["r"])
            if abs(val)>Decimal("0.1"):continue
            x.append((ts,val))
        except (ValueError,KeyError,TypeError,InvalidOperation):continue
    x.sort(key=lambda x:x[0],reverse=True)
    uniq={}
    for t,rate in x:uniq.setdefault(t,rate)
    return list(uniq.items())[:11]

def economic(book_spot,book_fut,contract,funding_rates,now_ms_spot,now_ms_fut,amount=TARGET_USDT):
    multiplier=D(contract.get("quanto_multiplier",0))
    rate=D(contract.get("funding_rate",0))
    if multiplier<=0 or rate<=0 or contract.get("in_delisting") is not False:
        return None
    if contract.get("type")!="direct" or int(contract.get("funding_interval",0))<=0:
        return None
    valid,clock=valid_time(book_spot,book_fut,now_ms_spot,now_ms_fut)
    if not valid:return {"status":"STALE_OR_DESYNCHRONIZED_BOOK","clock":clock}
    sas=levels(book_spot,False,"asks"); sbs=levels(book_spot,False,"bids")
    fbs=levels(book_fut,True,"bids"); fas=levels(book_fut,True,"asks")
    if not (sas and sbs and fbs and fas):return {"status":"EMPTY_BOOK","clock":clock}
    if not (sbs[0][0]<sas[0][0] and fbs[0][0]<fas[0][0]):
        return {"status":"CROSSED_BOOK","clock":clock}
    if abs(fbs[0][0]/sas[0][0]-1)>Decimal(".05"):
        return {"status":"INCOMPARABLE_SPOT_PERP_PRICE","clock":clock}
    contract_count=int(amount/(sas[0][0]*multiplier))
    if contract_count<int(contract.get("order_size_min",1)) or contract_count<=0:
        return {"status":"BELOW_MIN_CONTRACTS","clock":clock}
    qty=multiplier*contract_count
    buyspot=walk(sas,qty)
    sellperp=walk(fbs,qty,multiplier)
    sellspot=walk(sbs,qty)
    coverperp=walk(fas,qty,multiplier)
    if None in (buyspot,sellperp,sellspot,coverperp):
        return {"status":"INSUFFICIENT_L2_DEPTH","contracts":contract_count,"clock":clock}
    book_roundtrip=(sellspot-buyspot)+(sellperp-coverperp)
    trading_fees=SPOT_TAKER*(buyspot+sellspot)+FUT_TAKER*(sellperp+coverperp)
    exit_now=book_roundtrip-trading_fees
    upcoming=rate*sellperp
    history=rates(funding_rates,max(now_ms_spot,now_ms_fut))
    hist_sum=sum((z for _,z in history),Decimal(0))
    historical_11_same_notional=hist_sum*sellperp
    breakeven=((-exit_now)/upcoming) if upcoming>0 else None
    return {
        "status":"FORWARD_WATCH_NOT_FILL", "contract":contract["name"],
        "contract_count":contract_count,"qty_base":str(qty),
        "spot_entry_usdt":str(buyspot),"future_short_notional_usdt":str(sellperp),
        "spot_exit_value_usdt":str(sellspot),"future_cover_usdt":str(coverperp),
        "roundtrip_book_gross_usdt":str(book_roundtrip),"four_taker_fees_usdt":str(trading_fees),
        "immediate_liquidation_net_usdt":str(exit_now),
        "quoted_next_funding_rate":str(rate),"quoted_single_payment_usdt":str(upcoming),
        "quoted_payments_for_breakeven":str(breakeven) if breakeven is not None else None,
        "historic_paid_points":len(history),"historic_paid_rate_sum":str(hist_sum),
        "historic_11_same_notional_usdt":str(historical_11_same_notional),
        "historic_11_vs_today_book_close_net_usdt":str(exit_now+historical_11_same_notional),
        "historical_timestamps_utc_s":[x for x,_ in history],
        "next_scheduled_funding_utc_s":contract.get("funding_next_apply"),
        "funding_interval_seconds":contract.get("funding_interval"),
        "clock":clock, "owner_eligible":False,
        "eligibility_reason":"ACCOUNT_AND_RUB_USDT_ROUTE_NOT_VERIFIED",
        "trade_status":"ZERO_ORDERS_NO_PAPER_FILL"
    }

def survey():
    fut,ft,fd=public_json(ROOT+"/futures/usdt/tickers")
    spot,st,sd=public_json(ROOT+"/spot/tickers")
    chosen=shortlist(fut,spot)
    out=[]
    for pick in chosen:
        a=pick["pair"]
        try:
            so,srecv,sh=public_json(ROOT+"/spot/order_book?currency_pair="+a+"&limit=10&with_id=true")
            fu,frecv,fh=public_json(ROOT+"/futures/usdt/order_book?contract="+a+"&limit=10&with_id=true")
            ct,crecv,ch=public_json(ROOT+"/futures/usdt/contracts/"+a)
            hist,hrecv,hh=public_json(ROOT+"/futures/usdt/funding_rate?contract="+a+"&limit=11")
            if not isinstance(ct,dict) or not isinstance(hist,list):raise ValueError("BAD_CONTRACT_OR_HISTORY")
            econ=economic(so,fu,ct,hist,srecv,frecv)
            out.append({"asset":pick["asset"],"pair":a,
                "screened_current_rate":str(pick["funding_rate"]),
                "scanned_entry_spread_bp":str((pick["future_bid"]/pick["spot_ask"]-1)*10000),
                "economic":econ,"source_digests":[sh,fh,ch,hh]})
        except (ValueError,OSError,TypeError,KeyError,IndexError,InvalidOperation) as err:
            out.append({"pair":a,"status":"DATA_BLOCKED","error_class":type(err).__name__})
    watches=[x for x in out if isinstance(x.get("economic"),dict) and x["economic"].get("status")=="FORWARD_WATCH_NOT_FILL"]
    return {
        "schema":"NORM03B_M2_GATE_FUNDING_FORWARD_READONLY_V1",
        "source_timestamp_utc_ms":int(time.time()*1000),
        "providers":["Gate public spot","Gate public USDT perpetual","Gate public settled funding history"],
        "all_perpetuals":len(fut), "all_spots":len(spot), "screened_positive_rate_and_matching_pair":len(chosen),
        "forward_watches":len(watches),
        "positive_projected_one_payment_after_all_exit_costs":sum(
            D(x["economic"]["immediate_liquidation_net_usdt"])+D(x["economic"]["quoted_single_payment_usdt"])>0
            for x in watches),
        "positive_historic_11_refund_of_current_liquidation":sum(
            D(x["economic"]["historic_11_vs_today_book_close_net_usdt"])>0
            for x in watches),
        "results":out,"bulk_source_digest_sha256":[fd,sd],
        "model_limitations":["current funding estimate may change before settlement",
          "past 11 funding payments predate the observed quote and are not our executed PnL",
          "no proof of real spot/perp simultaneous fills","ticker match is not proof of identical token",
          "no owner account, RUB conversion, futures margin or borrowing permissions validated",
          "future basis is not guaranteed to converge"],
        "virtual_rub_account_unchanged":"50000", "paper_fills":0,"real_orders":0
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    report=survey()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:report[k] for k in ["all_perpetuals","all_spots","screened_positive_rate_and_matching_pair","forward_watches","positive_projected_one_payment_after_all_exit_costs","positive_historic_11_refund_of_current_liquidation","paper_fills","real_orders"]}))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
