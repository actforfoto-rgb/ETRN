#!/usr/bin/env python3
"""NORM 03B: quote-synchronized spot cross-venue arb, READ ONLY.

Forensic successor to historic 22 "executable" LTC/AVAX/FIL/ARB candidates.
These were from sequential CCXT L2 polls without common observation-time gate.
Only PUBLIC price ladders and bounded source timestamp checks are used here.
No trading, no balances, no authenticated API and no claim of actual fills.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

SCHEMA="NORM03B_PREFUNDED_SPOT_PAIR_TIME_GATED_V1"
ASSETS=("LTC","AVAX","FIL","ARB")
CAPITAL_USDT=(Decimal("100"),Decimal("1000"))
FEE={"BITGET":Decimal("0.001"),"HTX":Decimal("0.002")}
EXTRA_SLIPPAGE_PER_LEG=Decimal("0.0005")  # 5bp for each side, separate from L2
REBALANCE_RESERVE_USDT=Decimal("1.00")  # conservative scenario, NOT verified tariff
MAX_BOOK_AGE_MS=2000
MAX_MARKET_SKEW_MS=1000
MAX_RECEIPT_SKEW_MS=1500
MAX_CLOCK_AHEAD_MS=250
MAX_BYTES=400_000

def D(value):
    try:
        x=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError) as exc:
        raise ValueError("invalid number") from exc
    if not x.is_finite():raise ValueError("nonfinite number")
    return x

def raw_api(venue,asset):
    if venue=="BITGET":
        url=f"https://api.bitget.com/api/v3/market/orderbook?category=SPOT&symbol={asset}USDT&limit=20"
    elif venue=="HTX":
        url=f"https://api.huobi.pro/market/depth?symbol={asset.lower()}usdt&type=step0&depth=20"
    else:
        raise ValueError("not an approved public venue")
    req=Request(url,headers={"User-Agent":"NORM03B-ECONOMIC-ONLY/1.0","Accept":"application/json"},method="GET")
    started=int(time.time()*1000)
    with urlopen(req,timeout=7) as response:
        if response.status!=200:raise ValueError("bad HTTP status")
        body=response.read(MAX_BYTES+1)
    received=int(time.time()*1000)
    if len(body)>MAX_BYTES:raise ValueError("oversize public response")
    return json.loads(body),{"venue":venue,"asset":asset,"url":url,
                              "started_utc_ms":started,"received_utc_ms":received,
                              "body_sha256":hashlib.sha256(body).hexdigest()}

def parse_book(venue,asset,payload,receipt):
    if receipt["venue"]!=venue or receipt["asset"]!=asset:
        raise ValueError("receipt identity mismatch")
    if venue=="BITGET":
        if payload.get("code")!="00000":raise ValueError("bitget response not OK")
        o=payload["data"]
        timestamp=int(o["ts"])  # official book generation time, not requestTime
        bids,asks=o["b"],o["a"]
    elif venue=="HTX":
        if payload.get("status")!="ok":raise ValueError("HTX response not OK")
        o=payload["tick"]
        timestamp=int(o.get("ts",payload.get("ts",0)))
        bids,asks=o["bids"],o["asks"]
    else:
        raise ValueError("unsupported exchange")
    def ladders(rows,reverse):
        if not isinstance(rows,list) or not rows or len(rows)>200:
            raise ValueError("missing or oversized L2")
        seen=[]
        for row in rows:
            px,qty=D(row[0]),D(row[1])
            if px<=0 or qty<=0: raise ValueError("invalid px or qty")
            seen.append((px,qty))
        if any(seen[i][0]<=seen[i+1][0] if reverse else seen[i][0]>=seen[i+1][0]
               for i in range(len(seen)-1)):
            raise ValueError("unsorted/duplicate depth")
        return seen
    bid,ask=ladders(bids,True),ladders(asks,False)
    if bid[0][0]>=ask[0][0]:
        raise ValueError("crossed venue book")
    return {"venue":venue,"asset":asset,"exchange_utc_ms":timestamp,
            "received_utc_ms":int(receipt["received_utc_ms"]),
            "started_utc_ms":int(receipt["started_utc_ms"]),
            "body_sha256":receipt["body_sha256"],
            "bids":bid,"asks":ask}

def book_pair_gate(buy,sell):
    if buy["asset"]!=sell["asset"] or buy["venue"]==sell["venue"]:
        return {"status":"NOT_SAME_ASSET_OR_DIFFERENT_VENUES"}
    rb=int(buy["received_utc_ms"]); rs=int(sell["received_utc_ms"])
    tb=int(buy["exchange_utc_ms"]);ts=int(sell["exchange_utc_ms"])
    age_b=rb-tb;age_s=rs-ts
    skew=abs(tb-ts);receipt_skew=abs(rb-rs)
    checks={"age_buy_ms":age_b,"age_sell_ms":age_s,
            "market_timestamp_skew_ms":skew,"receipt_timestamp_skew_ms":receipt_skew}
    if (not -MAX_CLOCK_AHEAD_MS<=age_b<=MAX_BOOK_AGE_MS or
        not -MAX_CLOCK_AHEAD_MS<=age_s<=MAX_BOOK_AGE_MS):
        return {"status":"STALE_OR_FUTURE_BOOK",**checks}
    if skew>MAX_MARKET_SKEW_MS or receipt_skew>MAX_RECEIPT_SKEW_MS:
        return {"status":"NOT_SIMULTANEOUS_L2",**checks}
    return {"status":"SYNCHRONIZED_PUBLIC_QUOTE",**checks}

def depth_buy(asks,capital):
    rem=D(capital);qty=Decimal(0)
    for px,size in asks:
        take=min(rem,px*size)
        qty+=take/px
        rem-=take
        if rem<=Decimal("0.00000001"):
            return qty
    return None

def depth_sell(bids,qty):
    rem=D(qty);rev=Decimal(0)
    for px,size in bids:
        take=min(rem,size)
        rev+=take*px;rem-=take
        if rem<=Decimal("0.0000000001"):
            return rev
    return None

def evaluate(buy,sell,capital):
    pair=book_pair_gate(buy,sell)
    report={"buy_venue":buy["venue"],"sell_venue":sell["venue"],
            "asset":buy["asset"],"capital_usdt":str(capital),
            "pair_gate":pair,
            "owner_account_permission_verified":False,"owner_inventories_verified":False,
            "network_transfer_live_verified":False,
            "real_orders":0,"paper_fills":0,
            "decision":"NO_TRADE"}
    if pair["status"]!="SYNCHRONIZED_PUBLIC_QUOTE":
        report["status"]="REJECT_CAUSAL_TIMING"
        return report
    base=depth_buy(buy["asks"],capital)
    if base is None:
        report["status"]="REJECT_BUY_DEPTH"
        return report
    proceeds=depth_sell(sell["bids"],base)
    if proceeds is None:
        report["status"]="REJECT_SELL_DEPTH"
        return report
    gross=proceeds-capital
    fees=capital*FEE[buy["venue"]]+proceeds*FEE[sell["venue"]]
    slippage=(capital+proceeds)*EXTRA_SLIPPAGE_PER_LEG
    net=gross-fees-slippage-REBALANCE_RESERVE_USDT
    report.update({
        "status":"PUBLIC_QUOTE_POSITIVE_UNVERIFIED" if net>0 else "NO_POSITIVE_NET_AFTER_FULL_DEPTH",
        "base_qty":str(base),"gross_price_difference_usdt":str(gross),
        "taker_fees_usdt":str(fees),
        "extra_5bp_each_leg_usdt":str(slippage),
        "rebalance_reserve_scenario_usdt":str(REBALANCE_RESERVE_USDT),
        "net_after_model_costs_usdt":str(net),
        "net_after_model_costs_bps":str(net/capital*Decimal(10000)),
        "buy_book_sha256":buy["body_sha256"],"sell_book_sha256":sell["body_sha256"],
        "quote_not_execution":True
    })
    return report

def observe(rounds=2,seconds_between=1.0,assets=ASSETS):
    if not 1<=rounds<=4:raise ValueError("round bounds")
    if not 0<=seconds_between<=10:raise ValueError("interval bounds")
    outputs=[]
    for cycle in range(rounds):
        if cycle:time.sleep(seconds_between)
        tasks=[(asset,venue) for asset in assets for venue in ("BITGET","HTX")]
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending={k:pool.submit(raw_api,k[1],k[0]) for k in tasks}
            frames={}
            errors=[]
            for (asset,venue),future in pending.items():
                try:
                    payload,receipt=future.result()
                    frames[(asset,venue)]=parse_book(venue,asset,payload,receipt)
                except (OSError,TypeError,KeyError,ValueError,TimeoutError,IndexError) as exc:
                    errors.append({"asset":asset,"venue":venue,"error_type":type(exc).__name__})
        results=[]
        public_books=[]
        for (asset,venue),book in frames.items():
            # Public prices/depth are retained so calculations can be audited.
            public_books.append({**{k:v for k,v in book.items() if k not in ("bids","asks")},
                                 "bids":[[str(p),str(q)] for p,q in book["bids"]],
                                 "asks":[[str(p),str(q)] for p,q in book["asks"]]})
        for asset in assets:
            a=frames.get((asset,"BITGET"));b=frames.get((asset,"HTX"))
            if a is None or b is None:
                results.append({"asset":asset,"status":"VENUE_PUBLIC_L2_UNAVAILABLE",
                                "real_orders":0,"paper_fills":0})
                continue
            for buy,sell in ((a,b),(b,a)):
                for capital in CAPITAL_USDT:
                    results.append(evaluate(buy,sell,capital))
        outputs.append({"round":cycle,"utc":datetime.now(timezone.utc).isoformat(),
                        "books":public_books,"errors":errors,"evaluations":results})
    # All examined pairs and negative cases are kept, never only best observations.
    positives=[r for x in outputs for r in x["evaluations"]
               if r.get("status")=="PUBLIC_QUOTE_POSITIVE_UNVERIFIED"]
    confirmations={}
    for asset in assets:
        for buy,sell in (("BITGET","HTX"),("HTX","BITGET")):
            for cap in CAPITAL_USDT:
                key=f"{asset}:{buy}:{sell}:{cap}"
                matched=[next((r for r in x["evaluations"] if
                               r.get("asset")==asset and r.get("buy_venue")==buy and
                               r.get("sell_venue")==sell and
                               r.get("capital_usdt")==str(cap)),None) for x in outputs]
                confirmations[key]={"observed_rounds":len(matched),
                    "causal_pair_rounds":sum(m is not None and m.get("pair_gate",{}).get("status")=="SYNCHRONIZED_PUBLIC_QUOTE" for m in matched),
                    "positive_net_rounds":sum(m is not None and m.get("status")=="PUBLIC_QUOTE_POSITIVE_UNVERIFIED" for m in matched)}
    return {
        "schema":SCHEMA,"observed_utc":datetime.now(timezone.utc).isoformat(),
        "assets":list(assets),"requested_rounds":rounds,
        "positive_public_quote_screens":len(positives),
        "positive_repeated_same_route_and_cap":sum(v["positive_net_rounds"]>=2 for v in confirmations.values()),
        "best_public_quote_candidates":sorted(positives,key=lambda r:D(r["net_after_model_costs_usdt"]),reverse=True)[:8],
        "confirmations":confirmations,"rounds":outputs,
        "mode":"READ_ONLY_PUBLIC_QUOTE_NOT_FILL",
        "fees_and_rebalance_are_scenarios_not_owner_verified":True,
        "linked_capital_eligibility_verified":False,
        "real_orders":0,"paper_fills":0,
        "virtual_equity_rub":50000
    }

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--rounds",default=2,type=int)
    args=parser.parse_args()
    out=observe(rounds=args.rounds)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({
        "status":out["mode"],
        "assets":out["assets"],
        "positive_public_quote_screens":out["positive_public_quote_screens"],
        "positive_repeated_same_route_and_cap":out["positive_repeated_same_route_and_cap"],
        "valid_pair_checks":sum(1 for round in out["rounds"] for
              r in round["evaluations"] if r.get("pair_gate",{}).get("status")=="SYNCHRONIZED_PUBLIC_QUOTE"),
        "real_orders":0,"paper_fills":0
    },sort_keys=True))

if __name__=="__main__":
    main()
