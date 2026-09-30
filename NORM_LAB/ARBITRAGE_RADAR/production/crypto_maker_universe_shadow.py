from __future__ import annotations

import csv, json, math, statistics, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT.parent/"results"
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)

RESEARCH=RESULTS/"crypto_maker_taker_180d.json"
STATE_FILE=STATE_DIR/"crypto_maker_universe_state.json"
LAST_FILE=STATE_DIR/"crypto_maker_universe_last.json"
LEDGER_FILE=STATE_DIR/"crypto_maker_universe_ledger.csv"
TS_FILE=STATE_DIR/"crypto_maker_universe_timeseries.csv"

MAKER_FEE={"OKX":0.0002,"BITGET":0.0002,"GATE":0.0002,"MEXC":0.0006}
TAKER_FEE={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008}

NOTIONAL=1000.0
MAX_ROUTES=8
MIN_HOLDOUT_N=4
MIN_HOLDOUT_POSITIVE=70.0
MIN_HOLDOUT_MEDIAN_BPS=4.0
MIN_HOLDOUT_AGG_BPS=20.0
HIST_BARS=72
SAMPLES=12
SLEEP_SEC=5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=12*3600
MAX_MAKER_WAIT_SEC=15*60
MAKER_FILL_COVERAGE_MULT=2.0
EXTRA_BUFFER_BPS=4.0
MIN_EXPECTED_NET_BPS=4.0
MAX_QUOTE_GAP_MS=750

LEDGER_FIELDS=[
 "utc","event","route","base","maker_venue","hedge_venue","direction",
 "maker_limit","maker_fill","hedge_fill","entry_z","exit_z",
 "expected_net_bps","funding_bps","realized_net_bps","hold_sec",
 "maker_wait_sec","reason"
]
TS_FIELDS=[
 "utc","sample","route","base","maker_venue","hedge_venue","phase",
 "maker_bid","maker_ask","hedge_bid","hedge_ask","mid_basis_bps","z",
 "baseline_mean_bps","baseline_sd_bps","quote_gap_ms",
 "maker_latency_ms","hedge_latency_ms","expected_long_maker_bps",
 "expected_short_maker_bps","event"
]

def utc(): return datetime.now(timezone.utc).isoformat()

def load_research():
    arr=json.loads(RESEARCH.read_text(encoding="utf-8"))
    good=[]
    for r in arr:
        h=r.get("holdout") or {}
        if not r.get("holdout_pass"):continue
        if int(h.get("n") or 0)<MIN_HOLDOUT_N:continue
        if float(h.get("positive_pct") or 0)<MIN_HOLDOUT_POSITIVE:continue
        if float(h.get("median_net_bps") or 0)<MIN_HOLDOUT_MEDIAN_BPS:continue
        if float(h.get("aggregate_net_bps") or 0)<MIN_HOLDOUT_AGG_BPS:continue
        if r["maker_venue"]=="HTX" or r["hedge_venue"]=="HTX":continue
        score=(float(h["median_net_bps"])*math.sqrt(int(h["n"]))
               *float(h["positive_pct"])/100)
        good.append({**r,"live_score":score})
    good.sort(key=lambda r:(r["live_score"],(r.get("holdout") or {}).get("aggregate_net_bps",0)),reverse=True)
    # Avoid duplicate symmetric routes for same base/pair; keep best maker direction.
    selected=[];seen=set()
    for r in good:
        pair=(r["base"],tuple(sorted([r["maker_venue"],r["hedge_venue"]])))
        if pair in seen:continue
        seen.add(pair);selected.append(r)
        if len(selected)>=MAX_ROUTES:break
    return selected

def make(venue,base):
    ex=getattr(ccxt,venue.lower())({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    ms=[m for m in ex.markets.values()
        if m.get("swap") and m.get("linear") and m.get("active",True)
        and m.get("base")==base and m.get("quote")=="USDT"]
    if not ms:raise RuntimeError(f"{venue}: {base}/USDT swap unavailable")
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
    t0=int(time.time()*1000)
    ob=ex.fetch_order_book(sym,50)
    t1=int(time.time()*1000)
    bids=ob.get("bids") or [];asks=ob.get("asks") or []
    if not bids or not asks:raise RuntimeError("empty book")
    out={"best_bid":float(bids[0][0]),"best_ask":float(asks[0][0]),
         "bid_vwap":vwap(bids,NOTIONAL),"ask_vwap":vwap(asks,NOTIONAL),
         "mid_ts":(t0+t1)//2,"latency_ms":t1-t0}
    if out["bid_vwap"] is None or out["ask_vwap"] is None:raise RuntimeError("insufficient depth")
    return out

def paired_books(aex,asym,bex,bsym):
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa=pool.submit(book,aex,asym);fb=pool.submit(book,bex,bsym)
        A=fa.result();B=fb.result()
    return A,B,abs(A["mid_ts"]-B["mid_ts"])

def hist(ex,sym):
    rr=ex.fetch_ohlcv(sym,"1h",None,HIST_BARS)
    return {int(x[0]):{"close":float(x[4]),"volume":float(x[5] or 0)}
            for x in rr if len(x)>=6}

def baseline(aex,asym,bex,bsym):
    A=hist(aex,asym);B=hist(bex,bsym)
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0][-HIST_BARS:]
    xs=[(B[t]["close"]/A[t]["close"]-1)*10000 for t in ts]
    if len(xs)<36:raise RuntimeError("baseline too short")
    sd=statistics.pstdev(xs)
    if sd<=1e-9:raise RuntimeError("zero baseline sd")
    return {"mean":statistics.fmean(xs),"sd":sd,"n":len(xs)}

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"routes":{}}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def recent_trades(ex,sym,since_ms):
    try: rr=ex.fetch_trades(sym,since_ms,100)
    except Exception:return []
    out=[]
    for x in rr:
        try:out.append({"ts":int(x.get("timestamp") or 0),
                        "price":float(x.get("price")),
                        "amount":float(x.get("amount") or 0)})
        except:pass
    return out

def fill_evidence(side,px,trades,target_qty):
    eligible=([t for t in trades if t["price"]<px] if side=="BUY"
              else [t for t in trades if t["price"]>px])
    qty=sum(max(0.0,t["amount"]) for t in eligible)
    req=target_qty*MAKER_FILL_COVERAGE_MULT
    return {"filled":req>0 and qty>=req,"through_qty":qty,"required_qty":req}

def route_cost_bps(maker,hedge):
    # maker entry; hedge taker entry; BOTH exits taker + buffer.
    return (MAKER_FEE[maker]+TAKER_FEE[hedge]+TAKER_FEE[maker]+TAKER_FEE[hedge])*10000+EXTRA_BUFFER_BPS

def funding_since(ex,sym,since_ms):
    if not ex.has.get("fetchFundingRateHistory"):return 0.0
    try:
        rr=ex.fetch_funding_rate_history(sym,since_ms,100)
        return sum(float(x.get("fundingRate") or 0)
                   for x in rr if int(x.get("timestamp") or 0)>since_ms)
    except:return 0.0

def main():
    selected=load_research()
    prepared=[]
    ex_cache={}
    for r in selected:
        base=r["base"];m=r["maker_venue"];h=r["hedge_venue"]
        try:
            mk=(m,base);hk=(h,base)
            if mk not in ex_cache:ex_cache[mk]=make(m,base)
            if hk not in ex_cache:ex_cache[hk]=make(h,base)
            mex,msym=ex_cache[mk];hex,hsym=ex_cache[hk]
            bl=baseline(mex,msym,hex,hsym)
            prepared.append({"route":f"{base}|{m}|{h}","base":base,"maker":m,"hedge":h,
                             "entry_z":float(r["entry_z"]),"mex":mex,"msym":msym,
                             "hex":hex,"hsym":hsym,"baseline":bl,
                             "cost_bps":route_cost_bps(m,h)})
        except Exception:
            continue

    st=load_state();samples=[]
    for sample_no in range(SAMPLES):
        now=time.time();nowms=int(now*1000)
        for r in prepared:
            rs=st["routes"].setdefault(r["route"],{"phase":"FLAT","pending":None,"position":None})
            try:
                M,H,gap=paired_books(r["mex"],r["msym"],r["hex"],r["hsym"])
                if gap>MAX_QUOTE_GAP_MS:
                    row={"utc":utc(),"sample":sample_no,"route":r["route"],"base":r["base"],
                         "maker_venue":r["maker"],"hedge_venue":r["hedge"],
                         "phase":rs["phase"],"quote_gap_ms":gap,"event":"STALE_REJECT"}
                    samples.append(row);append(TS_FILE,TS_FIELDS,row);continue
                mmid=(M["best_bid"]+M["best_ask"])/2
                hmid=(H["best_bid"]+H["best_ask"])/2
                x=(hmid/mmid-1)*10000
                z=(x-r["baseline"]["mean"])/r["baseline"]["sd"]
                cost=r["cost_bps"]
                # Passive maker entry at current top of maker book; hedge uses $1000 VWAP.
                # High basis: long maker venue at maker bid, short hedge venue taker bid.
                long_exec=(H["bid_vwap"]/M["best_bid"]-1)*10000
                exp_long=long_exec-r["baseline"]["mean"]-cost
                # Low basis: short maker venue at maker ask, long hedge venue taker ask.
                short_exec=(H["ask_vwap"]/M["best_ask"]-1)*10000
                exp_short=r["baseline"]["mean"]-short_exec-cost
                event=""

                if rs["phase"]=="FLAT":
                    if z>=r["entry_z"] and exp_long>=MIN_EXPECTED_NET_BPS:
                        side="BUY";limit_px=M["best_bid"];direction="LONG_MAKER_SHORT_HEDGE";exp=exp_long
                    elif z<=-r["entry_z"] and exp_short>=MIN_EXPECTED_NET_BPS:
                        side="SELL";limit_px=M["best_ask"];direction="SHORT_MAKER_LONG_HEDGE";exp=exp_short
                    else:
                        side=None
                    if side:
                        rs.update({"phase":"ENTRY_MAKER_WAIT",
                                   "pending":{"side":side,"limit":limit_px,"placed_ts":now,"placed_ms":nowms,
                                              "target_qty":NOTIONAL/limit_px,"direction":direction,
                                              "entry_z":z,"expected_net_bps":exp},
                                   "position":None})
                        event="ENTRY_MAKER_POSTED"
                        append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":event,"route":r["route"],
                          "base":r["base"],"maker_venue":r["maker"],"hedge_venue":r["hedge"],
                          "direction":direction,"maker_limit":limit_px,"entry_z":z,
                          "expected_net_bps":exp,"reason":"VALIDATED_MAKER_SIGNAL"})

                elif rs["phase"]=="ENTRY_MAKER_WAIT":
                    p=rs["pending"];wait=now-p["placed_ts"]
                    ev=fill_evidence(p["side"],p["limit"],recent_trades(r["mex"],r["msym"],p["placed_ms"]),p["target_qty"])
                    if ev["filled"]:
                        hedge_fill=H["bid_vwap"] if p["direction"]=="LONG_MAKER_SHORT_HEDGE" else H["ask_vwap"]
                        rs.update({"phase":"HEDGED","pending":None,
                                   "position":{"direction":p["direction"],"opened_ts":now,
                                               "maker_entry":p["limit"],"hedge_entry":hedge_fill,
                                               "entry_z":p["entry_z"],"expected_net_bps":p["expected_net_bps"]}})
                        event="ENTRY_FILLED_HEDGED"
                        append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":event,"route":r["route"],
                          "base":r["base"],"maker_venue":r["maker"],"hedge_venue":r["hedge"],
                          "direction":p["direction"],"maker_limit":p["limit"],"maker_fill":p["limit"],
                          "hedge_fill":hedge_fill,"entry_z":p["entry_z"],"expected_net_bps":p["expected_net_bps"],
                          "maker_wait_sec":wait,"reason":f"TRADE_THROUGH qty={ev['through_qty']:.8f}"})
                    elif wait>=MAX_MAKER_WAIT_SEC or abs(z)<r["entry_z"]*.75:
                        rs.update({"phase":"FLAT","pending":None,"position":None});event="ENTRY_CANCEL"

                elif rs["phase"]=="HEDGED":
                    p=rs["position"];hold=now-p["opened_ts"]
                    if p["direction"]=="LONG_MAKER_SHORT_HEDGE":
                        raw=((M["bid_vwap"]/p["maker_entry"]-1)+(1-H["ask_vwap"]/p["hedge_entry"]))*10000
                        f=(-funding_since(r["mex"],r["msym"],int(p["opened_ts"]*1000))
                           +funding_since(r["hex"],r["hsym"],int(p["opened_ts"]*1000)))*10000
                    else:
                        raw=((1-M["ask_vwap"]/p["maker_entry"])+(H["bid_vwap"]/p["hedge_entry"]-1))*10000
                        f=(funding_since(r["mex"],r["msym"],int(p["opened_ts"]*1000))
                           -funding_since(r["hex"],r["hsym"],int(p["opened_ts"]*1000)))*10000
                    pnl=raw+f-r["cost_bps"]
                    reason=None
                    if abs(z)<=EXIT_Z:reason="CONVERGENCE"
                    elif abs(z)>=STOP_Z:reason="Z_STOP"
                    elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
                    if reason:
                        # Conservative: both exits taker immediately.
                        event="CLOSE_TAKER_TAKER"
                        append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":event,"route":r["route"],
                          "base":r["base"],"maker_venue":r["maker"],"hedge_venue":r["hedge"],
                          "direction":p["direction"],"entry_z":p["entry_z"],"exit_z":z,
                          "expected_net_bps":p["expected_net_bps"],"funding_bps":f,
                          "realized_net_bps":pnl,"hold_sec":hold,"reason":reason})
                        rs.update({"phase":"FLAT","pending":None,"position":None})

                row={"utc":utc(),"sample":sample_no,"route":r["route"],"base":r["base"],
                     "maker_venue":r["maker"],"hedge_venue":r["hedge"],"phase":rs["phase"],
                     "maker_bid":M["best_bid"],"maker_ask":M["best_ask"],
                     "hedge_bid":H["bid_vwap"],"hedge_ask":H["ask_vwap"],
                     "mid_basis_bps":x,"z":z,"baseline_mean_bps":r["baseline"]["mean"],
                     "baseline_sd_bps":r["baseline"]["sd"],"quote_gap_ms":gap,
                     "maker_latency_ms":M["latency_ms"],"hedge_latency_ms":H["latency_ms"],
                     "expected_long_maker_bps":exp_long,"expected_short_maker_bps":exp_short,
                     "event":event}
                samples.append(row);append(TS_FILE,TS_FIELDS,row)
            except Exception as e:
                samples.append({"utc":utc(),"sample":sample_no,"route":r["route"],"error":f"{type(e).__name__}: {e}"})
        save_state(st)
        if sample_no<SAMPLES-1:time.sleep(SLEEP_SEC)

    report={"utc":utc(),"routes":[{k:v for k,v in r.items() if k not in ("mex","hex")} for r in prepared],
            "samples":samples[-100:],"state":st}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"routes":len(prepared),"phases":{k:v["phase"] for k,v in st["routes"].items()},
                      "last_samples":samples[-12:]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
