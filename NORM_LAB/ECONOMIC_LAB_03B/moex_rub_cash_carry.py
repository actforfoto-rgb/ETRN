#!/usr/bin/env python3
"""NORM 03B M8: RUB share spot-long / listed future short cash-and-carry gate.

MOEX TQBR vs FORTS RFUD, live metadata; order flow is READ-ONLY.
Ticker equality DOES NOT prove same economic underlying. Example: GOLD/GDZ6
is not a gold-backed 2.6 RUB share against a 4,000 RUB gold derivative.

The result is an economic candidate SCREEN, never a virtual fill:
- public ISS quotes are delayed
- corporate dividends/settlement may invalidate theoretical carry
- client tariff/financing and exact legal execution permissions unknown
"""
from __future__ import annotations

import argparse
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

FUTURE_API=(
  "https://iss.moex.com/iss/engines/futures/markets/forts/boards/rfud/"
  "securities.json?iss.meta=off&iss.only=securities,marketdata&"
  "securities.columns=SECID,SHORTNAME,ASSETCODE,LOTVOLUME,LASTTRADEDATE,"
  "INITIALMARGIN,MINSTEP,STEPPRICE,BUYSELLFEE&"
  "marketdata.columns=SECID,BID,OFFER,UPDATETIME,SYSTIME&limit=1000"
)
STOCK_API=(
  "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/"
  "securities.json?iss.meta=off&iss.only=marketdata,securities&"
  "securities.columns=SECID,LOTSIZE&"
  "marketdata.columns=SECID,BID,OFFER,UPDATETIME,SYSTIME&limit=1000"
)
CAP=Decimal("40000")
FEE_STOCK_SIDE=Decimal("0.001")
BROKER_FUT_ROUNDTRIP_STRESS=Decimal("10")
COST_RATES=(Decimal("0.14"), Decimal("0.18"))
MAX_EXPIRY_DAYS=365
MAX_PRICE_TO_PARITY=Decimal("1.25")
MIN_PRICE_TO_PARITY=Decimal("0.80")
MAX_EXCHANGE_SKEW_SECONDS=20
KNOWN_COLLISIONS={"GOLD"}  # GOLD stock name != GDZ6 gold futures underlying.

def d(v):
    if isinstance(v,bool) or v is None:raise ValueError("INVALID_NUMBER")
    x=Decimal(str(v))
    if not x.is_finite():raise ValueError("NONFINITE")
    return x

def pull(url):
    if url not in (FUTURE_API, STOCK_API):raise ValueError("READ_ONLY_SOURCE_WHITELIST")
    req=Request(url,headers={"User-Agent":"NORM-03B-RUB-FUT-CARRY/1","Accept":"application/json"},method="GET")
    with urlopen(req,timeout=14) as r:
        if r.status!=200:raise ValueError("SOURCE_HTTP_STATUS")
        body=r.read(1800001)
    if len(body)>1800000:raise ValueError("SOURCE_TOO_LARGE")
    return json.loads(body),hashlib.sha256(body).hexdigest()

def rows(obj,name):
    block=obj[name]
    cols=block["columns"]
    if len(cols)!=len(set(cols)):raise ValueError("DUPLICATE_KEYS")
    for values in block["data"]:
        if len(values)!=len(cols):raise ValueError("INVALID_ROWS")
        yield dict(zip(cols,values))

def timepoint(quote):
    # MSK as exchange-local clock; day comes from SYSTIME publication
    # within public ISS historical reporting. The exchange quote may be ~15min old.
    publication=quote["SYSTIME"]
    update=quote["UPDATETIME"]
    if not isinstance(publication,str) or not isinstance(update,str):
        raise ValueError("MISSING_SOURCE_CLOCK")
    day=publication[:10]
    return datetime.fromisoformat(day+"T"+update)

def time_gate(stock,future):
    try:
        a,b=timepoint(stock),timepoint(future)
        skew=abs((b-a).total_seconds())
        return skew<=MAX_EXCHANGE_SKEW_SECONDS,skew
    except (TypeError,ValueError,KeyError):return False,None

def screen(stockmeta,spot,future,perp,asof):
    asset=future.get("ASSETCODE","")
    if asset!=stockmeta.get("SECID") or asset!=spot.get("SECID"):
        return None,"NOT_IDENTICAL_UNDERLYING_TICKER"
    if asset in KNOWN_COLLISIONS:
        return None,"KNOWN_UNDERLYING_COLLISION"
    if not future.get("SHORTNAME","").startswith(asset+"-"):
        return None,"FUTURE_SHORT_NAME_MISMATCH"
    try:
        n=d(future["LOTVOLUME"])
        stock_lot=d(stockmeta["LOTSIZE"])
        if n<=0 or stock_lot<=0 or n!=n.to_integral_value() or stock_lot!=stock_lot.to_integral_value() or n%stock_lot!=0:
            return None,"SHARE_LOT_INCOMPATIBLE"
        ask=d(spot["OFFER"]);fut_bid=d(perp["BID"])
        spot_bid=d(spot["BID"]); fut_ask=d(perp["OFFER"])
        if min(ask,fut_bid,spot_bid,fut_ask)<=0 or ask<=spot_bid or fut_ask<=fut_bid:
            return None,"BAD_QUOTES"
        cost_stock=ask*n
        ratio=fut_bid/cost_stock
        if ratio>MAX_PRICE_TO_PARITY or ratio<MIN_PRICE_TO_PARITY:
            return None,"ECONOMICALLY_INCOMPARABLE_UNDERLYING"
        margin=d(future["INITIALMARGIN"])
        if margin<=0:return None,"BAD_MARGIN"
        linked=cost_stock+margin
        if linked>CAP:return None,"CASH_OR_CAPITAL_LIMIT"
        expiry=date.fromisoformat(future["LASTTRADEDATE"])
        days=(expiry-asof).days
        if not 0<days<=MAX_EXPIRY_DAYS:return None,"DATE_EXPIRED_OR_TOO_DISTANT"
        aligned,skew=time_gate(spot,perp)
        if not aligned:return None,"SOURCE_EXCHANGE_CLOCK_MISMATCH"
        future_trade_fee=2*d(future["BUYSELLFEE"])
        if future_trade_fee<0:return None,"NEGATIVE_FEE"
        stock_fee=cost_stock*FEE_STOCK_SIDE*2
        full_fees=stock_fee+future_trade_fee+BROKER_FUT_ROUNDTRIP_STRESS
        gross=fut_bid-cost_stock
        before_capital_cost=gross-full_fees
        apr_break_even=before_capital_cost*Decimal(365)/(linked*Decimal(days))
        net_by_apr={
            str(rate):str(before_capital_cost-linked*rate*Decimal(days)/Decimal(365))
            for rate in COST_RATES
        }
        return {
            "asset":asset,"future":future["SECID"],"expiry":future["LASTTRADEDATE"],
            "stock_shares_per_future":int(n),"stock_lot":int(stock_lot),
            "spot_ask_rub":str(ask),"future_bid_rub":str(fut_bid),
            "entry_spot_cash_rub":str(cost_stock),"futures_margin_rub":str(margin),
            "linked_capital_rub":str(linked),"days_to_expiry":days,
            "gross_basis_rub":str(gross),
            "fee_stock_two_sides_rub":str(stock_fee),
            "fee_future_exchange_two_sides_rub":str(future_trade_fee),
            "broker_futures_fee_stress_rub":str(BROKER_FUT_ROUNDTRIP_STRESS),
            "economic_before_capital_cost_rub":str(before_capital_cost),
            "break_even_capital_cost_apr":str(apr_break_even),
            "net_after_cost_of_capital_rub":net_by_apr,
            "published_spot_time":spot["SYSTIME"],
            "published_future_time":perp["SYSTIME"],
            "exchange_quote_skew_sec":skew,
            "settlement_type_verified":False,
            "dividend_corporate_actions_verified":False,
            "economic_underlying_identity_verified_by_contract_id":False,
            "broker_client_trading_tariff_verified":False,
            "quote_book_depth_verified":False,
            "source_quotes_delayed":True,
            "paper_fill":False,"real_order":False,
            "status":"RUB_CARRY_INDICATIVE_NOT_OWNER_EXECUTABLE"
        }, "SCREENED"
    except (KeyError,TypeError,InvalidOperation,ValueError,OverflowError):
        return None,"INVALID_METADATA"

def analyse(jf,js,asof):
    fut=list(rows(jf,"securities"))
    fbook={x["SECID"]:x for x in rows(jf,"marketdata")}
    shares={x["SECID"]:x for x in rows(js,"securities")}
    sbook={x["SECID"]:x for x in rows(js,"marketdata")}
    results=[];rejections={}
    for f in fut:
        asset=f.get("ASSETCODE","")
        if asset not in shares or asset not in sbook or f["SECID"] not in fbook:
            continue
        result,reason=screen(shares[asset],sbook[asset],f,fbook[f["SECID"]],asof)
        if result is not None:results.append(result)
        else:rejections[reason]=rejections.get(reason,0)+1
    results.sort(key=lambda r:d(r["net_after_cost_of_capital_rub"]["0.14"]),reverse=True)
    return {
      "schema":"NORM03B_M8_MOEX_RUB_SPOT_FUTURES_CASH_CARRY_READONLY_V1",
      "asof_msk":asof.isoformat(),
      "total_futures":len(fut),
      "total_tqbr_shares":len(shares),
      "valid_rub_financeable_constructions":len(results),
      "false_underlying_collisions":rejections.get("KNOWN_UNDERLYING_COLLISION",0)+rejections.get("ECONOMICALLY_INCOMPARABLE_UNDERLYING",0),
      "positive_14pct_capital_cost":sum(d(r["net_after_cost_of_capital_rub"]["0.14"])>0 for r in results),
      "positive_18pct_capital_cost":sum(d(r["net_after_cost_of_capital_rub"]["0.18"])>0 for r in results),
      "best_after_costs":results[:15],
      "rejected_by_gate":rejections,
      "limits":["ticker match does not prove base asset identity",
               "cash/futures quotes are delayed public proxy, not executable",
               "futures settlement cash-vs-physical not verified",
               "dividends and corporate actions not priced",
               "financing 14/18pct annual is a scenario, not an available funding contract",
               "future short needs approved margin/broker route",
               "fees are scenario and omit final settlement specifics"],
      "position_source":"NO_OWNER_FUNDS_OR_ORDERS",
      "cash_rub":50000,"equity_rub":50000,
      "virtual_closed_cycles":0,"paper_fills":0,"real_orders":0
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    jf,d1=pull(FUTURE_API)
    js,d2=pull(STOCK_API)
    asof=datetime.now(timezone(timedelta(hours=3))).date()
    report=analyse(jf,js,asof)
    report["source_hashes_sha256"]={"futures":d1,"shares":d2}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:report[k] for k in [
        "total_futures","total_tqbr_shares","valid_rub_financeable_constructions",
        "false_underlying_collisions","positive_14pct_capital_cost",
        "positive_18pct_capital_cost","paper_fills","real_orders"
    ]},sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
