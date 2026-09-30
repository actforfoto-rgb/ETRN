from __future__ import annotations

import csv, json, math, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT.parent/"results"
STATE_DIR=ROOT/"event_state";STATE_DIR.mkdir(parents=True,exist_ok=True)

RESEARCH=RESULTS/"crypto_maker_taker_180d.json"
CSV_FILE=STATE_DIR/"crypto_maker_fill_calibration.csv"
LAST_FILE=STATE_DIR/"crypto_maker_fill_calibration_last.json"

NOTIONAL=1000.0
MAX_ROUTES=5
WAIT_SEC=30
FILL_COVERAGE_MULT=2.0
MAX_QUOTE_GAP_MS=750

FIELDS=[
 "utc","route","base","maker_venue","hedge_venue","side","maker_price","target_qty",
 "filled","through_qty","required_qty","wait_sec","initial_hedge_price","detected_hedge_price",
 "hedge_slippage_bps","initial_cross_basis_bps","detected_cross_basis_bps",
 "maker_latency_ms","hedge_latency_ms","quote_gap_ms"
]

def utc():return datetime.now(timezone.utc).isoformat()

def research_routes():
    arr=json.loads(RESEARCH.read_text(encoding="utf-8"))
    good=[]
    for r in arr:
        h=r.get("holdout") or {}
        if not r.get("holdout_pass"):continue
        if r["maker_venue"]=="HTX" or r["hedge_venue"]=="HTX":continue
        if int(h.get("n") or 0)<4:continue
        if float(h.get("positive_pct") or 0)<70:continue
        if float(h.get("median_net_bps") or 0)<4:continue
        score=float(h["median_net_bps"])*math.sqrt(int(h["n"]))
        good.append((score,r))
    good.sort(key=lambda x:x[0],reverse=True)
    out=[];seen=set()
    for _,r in good:
        k=(r["base"],r["maker_venue"],r["hedge_venue"])
        if k in seen:continue
        seen.add(k);out.append(r)
        if len(out)>=MAX_ROUTES:break
    return out

def make(venue,base):
    ex=getattr(ccxt,venue.lower())({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    ms=[m for m in ex.markets.values()
        if m.get("swap") and m.get("linear") and m.get("active",True)
        and m.get("base")==base and m.get("quote")=="USDT"]
    if not ms:raise RuntimeError("swap missing")
    return ex,ms[0]["symbol"]

def vwap(levels,quote):
    rem=quote;base=done=0.0
    for x in levels:
        px=float(x[0]);qty=float(x[1]);q=px*qty;take=min(rem,q)
        done+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None
    return done/base

def book(ex,sym):
    t0=int(time.time()*1000);ob=ex.fetch_order_book(sym,50);t1=int(time.time()*1000)
    bids=ob.get("bids") or [];asks=ob.get("asks") or []
    if not bids or not asks:raise RuntimeError("empty book")
    return {"bid":float(bids[0][0]),"ask":float(asks[0][0]),
            "bid_vwap":vwap(bids,NOTIONAL),"ask_vwap":vwap(asks,NOTIONAL),
            "mid_ts":(t0+t1)//2,"latency_ms":t1-t0}

def pair_books(mex,msym,hex,hsym):
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(book,mex,msym);b=pool.submit(book,hex,hsym)
        A=a.result();B=b.result()
    return A,B,abs(A["mid_ts"]-B["mid_ts"])

def recent(ex,sym,since_ms):
    try:rr=ex.fetch_trades(sym,since_ms,200)
    except:return []
    out=[]
    for x in rr:
        try:out.append({"price":float(x.get("price")),"amount":float(x.get("amount") or 0),
                        "ts":int(x.get("timestamp") or 0)})
        except:pass
    return out

def fill(side,px,trades,target):
    eligible=([x for x in trades if x["price"]<px] if side=="BUY"
              else [x for x in trades if x["price"]>px])
    q=sum(max(0,x["amount"]) for x in eligible);req=target*FILL_COVERAGE_MULT
    return q>=req and req>0,q,req

def append(row):
    new=not CSV_FILE.exists()
    with CSV_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in FIELDS})

def main():
    routes=research_routes();prepared=[]
    for r in routes:
        try:
            mex,msym=make(r["maker_venue"],r["base"])
            hex,hsym=make(r["hedge_venue"],r["base"])
            A,B,gap=pair_books(mex,msym,hex,hsym)
            if gap>MAX_QUOTE_GAP_MS:continue
            prepared.append({"r":r,"mex":mex,"msym":msym,"hex":hex,"hsym":hsym,
                             "A0":A,"B0":B,"gap":gap,"placed_ms":int(time.time()*1000)})
        except:continue

    time.sleep(WAIT_SEC)
    rows_out=[]
    for x in prepared:
        r=x["r"];A0=x["A0"];B0=x["B0"]
        trades=recent(x["mex"],x["msym"],x["placed_ms"])
        try:A1,B1,gap1=pair_books(x["mex"],x["msym"],x["hex"],x["hsym"])
        except:continue
        for side in ("BUY","SELL"):
            px=A0["bid"] if side=="BUY" else A0["ask"]
            target=NOTIONAL/px
            filled,through,req=fill(side,px,trades,target)
            if side=="BUY":
                h0=B0["bid_vwap"];h1=B1["bid_vwap"]
                hs=((h1/h0)-1)*10000 if h0 and h1 else None
                b0=(h0/px-1)*10000 if h0 else None
                b1=(h1/px-1)*10000 if h1 else None
            else:
                h0=B0["ask_vwap"];h1=B1["ask_vwap"]
                hs=((h1/h0)-1)*10000 if h0 and h1 else None
                b0=(h0/px-1)*10000 if h0 else None
                b1=(h1/px-1)*10000 if h1 else None
            row={"utc":utc(),"route":f"{r['base']}|{r['maker_venue']}|{r['hedge_venue']}",
                 "base":r["base"],"maker_venue":r["maker_venue"],"hedge_venue":r["hedge_venue"],
                 "side":side,"maker_price":px,"target_qty":target,"filled":filled,
                 "through_qty":through,"required_qty":req,"wait_sec":WAIT_SEC,
                 "initial_hedge_price":h0,"detected_hedge_price":h1,
                 "hedge_slippage_bps":hs,"initial_cross_basis_bps":b0,
                 "detected_cross_basis_bps":b1,
                 "maker_latency_ms":A1["latency_ms"],"hedge_latency_ms":B1["latency_ms"],
                 "quote_gap_ms":gap1}
            append(row);rows_out.append(row)
    report={"utc":utc(),"routes_requested":len(routes),"routes_tested":len(prepared),
            "observations":rows_out,
            "fills":sum(bool(r["filled"]) for r in rows_out)}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
