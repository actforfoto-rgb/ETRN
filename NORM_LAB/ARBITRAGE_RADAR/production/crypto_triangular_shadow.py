from __future__ import annotations

import csv, json, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
LAST_FILE=STATE_DIR/"crypto_triangular_last.json"
LEDGER_FILE=STATE_DIR/"crypto_triangular_opportunities.csv"

VENUES=["okx","bitget","gate","mexc"]
ASSETS=["ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH","SUI",
        "HBAR","WIF","APT","ARB","OP","UNI","DOT","FIL","ICP","AAVE","ETC"]

TAKER={
 "OKX":0.0010,
 "BITGET":0.0010,
 "GATE":0.0010,
 "MEXC":0.0005,
}
CAPITALS=[100.0,1000.0]
EXTRA_BUFFER_BPS=5.0
MIN_NET_BPS=2.0

FIELDS=[
 "utc","venue","anchor","asset","direction","capital_usdt","gross_bps",
 "fee_bps","buffer_bps","net_bps",
 "leg1_price","leg2_price","leg3_price","status"
]

def utc():return datetime.now(timezone.utc).isoformat()

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    return exid.upper(),ex

def find(ex,base,quote):
    ms=[m for m in ex.markets.values()
        if m.get("spot") and m.get("active",True)
        and m.get("base")==base and m.get("quote")==quote]
    return ms[0]["symbol"] if ms else None

def vwap_quote_to_base(asks,quote_amount):
    # Buy base with quote through asks. Returns base received and avg px.
    rem=quote_amount;base=0.0;done=0.0
    for x in asks:
        px=float(x[0]);qty=float(x[1])
        level_quote=px*qty
        take=min(rem,level_quote)
        base+=take/px;done+=take;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None,None
    return base,done/base

def vwap_base_to_quote(bids,base_amount):
    # Sell base for quote through bids. Returns quote received and avg px.
    rem=base_amount;quote=0.0;done=0.0
    for x in bids:
        px=float(x[0]);qty=float(x[1])
        take=min(rem,qty)
        quote+=take*px;done+=take;rem-=take
        if rem<=1e-12:break
    if rem>1e-9 or done<=0:return None,None
    return quote,quote/done

def book(ex,sym,venue):
    try:
        ob=ex.fetch_order_book(sym,50)
        if venue=="MEXC":time.sleep(0.20)
        return ob
    except Exception:
        return None

def append(rows):
    if not rows:return
    new=not LEDGER_FILE.exists()
    with LEDGER_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS)
        if new:w.writeheader()
        for r in rows:w.writerow({k:r.get(k,"") for k in FIELDS})

def scan_venue(name,ex):
    fee=TAKER[name]
    out=[];errors=[]
    anchors=["BTC","ETH"]

    usdt_books={}
    cross_books={}
    for anchor in anchors:
        a_usdt=find(ex,anchor,"USDT")
        if not a_usdt:continue
        ab=book(ex,a_usdt,name)
        if not ab:continue
        usdt_books[anchor]=(a_usdt,ab)

        for asset in ASSETS:
            if asset==anchor:continue
            a_sym=find(ex,asset,"USDT")
            x_sym=find(ex,asset,anchor)
            if not a_sym or not x_sym:continue
            if asset not in usdt_books:
                aub=book(ex,a_sym,name)
                if not aub:continue
                usdt_books[asset]=(a_sym,aub)
            xb=book(ex,x_sym,name)
            if not xb:continue
            cross_books[(asset,anchor)]=(x_sym,xb)

    for (asset,anchor),(x_sym,xb) in cross_books.items():
        _,anchor_ob=usdt_books[anchor]
        _,asset_ob=usdt_books[asset]
        for cap in CAPITALS:
            # A: USDT -> anchor -> asset -> USDT
            anchor_qty,p1=vwap_quote_to_base(anchor_ob["asks"],cap)
            if anchor_qty:
                anchor_after=anchor_qty*(1-fee)
                asset_qty,p2=vwap_quote_to_base(xb["asks"],anchor_after)
                if asset_qty:
                    asset_after=asset_qty*(1-fee)
                    final,p3=vwap_base_to_quote(asset_ob["bids"],asset_after)
                    if final:
                        final*=1-fee
                        gross=(final/cap-1)*10000
                        # gross already includes actual fee deductions above.
                        net=gross-EXTRA_BUFFER_BPS
                        out.append({
                          "utc":utc(),"venue":name,"anchor":anchor,"asset":asset,
                          "direction":"USDT->ANCHOR->ASSET->USDT","capital_usdt":cap,
                          "gross_bps":gross,"fee_bps":3*fee*10000,
                          "buffer_bps":EXTRA_BUFFER_BPS,"net_bps":net,
                          "leg1_price":p1,"leg2_price":p2,"leg3_price":p3,
                          "status":"EXECUTABLE_CANDIDATE" if net>=MIN_NET_BPS else "REJECT"
                        })

            # B: USDT -> asset -> anchor -> USDT
            asset_qty,p1=vwap_quote_to_base(asset_ob["asks"],cap)
            if asset_qty:
                asset_after=asset_qty*(1-fee)
                anchor_quote,p2=vwap_base_to_quote(xb["bids"],asset_after)
                if anchor_quote:
                    anchor_after=anchor_quote*(1-fee)
                    final,p3=vwap_base_to_quote(anchor_ob["bids"],anchor_after)
                    if final:
                        final*=1-fee
                        gross=(final/cap-1)*10000
                        net=gross-EXTRA_BUFFER_BPS
                        out.append({
                          "utc":utc(),"venue":name,"anchor":anchor,"asset":asset,
                          "direction":"USDT->ASSET->ANCHOR->USDT","capital_usdt":cap,
                          "gross_bps":gross,"fee_bps":3*fee*10000,
                          "buffer_bps":EXTRA_BUFFER_BPS,"net_bps":net,
                          "leg1_price":p1,"leg2_price":p2,"leg3_price":p3,
                          "status":"EXECUTABLE_CANDIDATE" if net>=MIN_NET_BPS else "REJECT"
                        })
    out.sort(key=lambda r:r["net_bps"],reverse=True)
    return out,errors

def main():
    allrows=[];coverage=[]
    for exid in VENUES:
        try:
            name,ex=build(exid)
            rows,errs=scan_venue(name,ex)
            allrows.extend(rows)
            coverage.append({"venue":name,"ok":True,"triangles":len(rows),"errors":len(errs)})
        except Exception as e:
            coverage.append({"venue":exid.upper(),"ok":False,"error":f"{type(e).__name__}: {e}"[:500]})
    allrows.sort(key=lambda r:r["net_bps"],reverse=True)
    positive=[r for r in allrows if r["net_bps"]>=MIN_NET_BPS]
    append(positive)
    report={"utc":utc(),"coverage":coverage,"tested":len(allrows),
            "positive_candidates":len(positive),"top":allrows[:50]}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"coverage":coverage,"tested":len(allrows),
                      "positive_candidates":len(positive),"top":allrows[:15]},
                     ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
