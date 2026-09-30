from __future__ import annotations

import csv, json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"op_gate_okx_state.json"
TS_FILE=STATE_DIR/"op_gate_okx_timeseries.csv"
LEDGER_FILE=STATE_DIR/"op_gate_okx_ledger.csv"
LAST_FILE=STATE_DIR/"op_gate_okx_last.json"

BASE="OP"
A_NAME="GATE"
B_NAME="OKX"
NOTIONAL=1000.0
FEE_A=0.0005
FEE_B=0.0005
ENTRY_Z=3.0
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=12*3600
EXTRA_BUFFER_BPS=8.0
SAMPLES=24
SLEEP_SEC=5
HIST_BARS=48

FIELDS=["utc","sample","a_bid","a_ask","b_bid","b_ask","mid_basis_bps","z",
        "baseline_mean_bps","baseline_sd_bps","exec_high_bps","exec_low_bps",
        "fee_bps","exit_spread_bps","expected_high_bps","expected_low_bps",
        "position"]
LEDGER_FIELDS=["utc","event","direction","entry_z","exit_z","entry_exec_bps",
               "expected_net_bps","realized_net_bps","hold_sec","reason"]

def utc():return datetime.now(timezone.utc).isoformat()

def make(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":12000})
    ex.load_markets()
    ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
        and m.get("active",True) and m.get("base")==BASE and m.get("quote")=="USDT"]
    if not ms:raise RuntimeError(f"{exid}: OP USDT linear swap missing")
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
    ob=ex.fetch_order_book(sym,50)
    bid=vwap(ob.get("bids") or [],NOTIONAL)
    ask=vwap(ob.get("asks") or [],NOTIONAL)
    if bid is None or ask is None:raise RuntimeError("insufficient depth")
    return bid,ask

def hist(ex,sym):
    since=int((time.time()-7*86400)*1000)
    out=[];cursor=since
    for _ in range(4):
        rr=ex.fetch_ohlcv(sym,"1h",cursor,200)
        if not rr:break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor:break
        cursor=mx+3600000
        if cursor>=int(time.time()*1000)-3600000:break
    return {int(x[0]):{"close":float(x[4]),"volume":float(x[5] or 0)}
            for x in out if len(x)>=6}

def baseline(aex,asym,bex,bsym):
    A=hist(aex,asym);B=hist(bex,bsym)
    ts=[t for t in sorted(set(A)&set(B))
        if A[t]["volume"]>0 and B[t]["volume"]>0][-HIST_BARS:]
    xs=[(B[t]["close"]/A[t]["close"]-1)*10000 for t in ts]
    if len(xs)<24:raise RuntimeError(f"baseline too short: {len(xs)}")
    return statistics.fmean(xs),statistics.pstdev(xs),len(xs)

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"position":None}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def main():
    aex,asym=make("gate")
    bex,bsym=make("okx")
    mean,sd,nbase=baseline(aex,asym,bex,bsym)
    state=load_state()
    rows=[]

    for i in range(SAMPLES):
        try:
            abid,aask=book(aex,asym)
            bbid,bask=book(bex,bsym)
            amid=(abid+aask)/2;bmid=(bbid+bask)/2
            x=(bmid/amid-1)*10000
            z=(x-mean)/sd if sd>1e-12 else 0.0
            hi=(bbid/aask-1)*10000
            lo=(bask/abid-1)*10000
            fees=2*(FEE_A+FEE_B)*10000
            exit_spread=((aask-abid)/amid+(bask-bbid)/bmid)*10000
            costs=fees+exit_spread+EXTRA_BUFFER_BPS
            expected_high=(hi-mean)-costs
            expected_low=(mean-lo)-costs

            pos=state.get("position")
            if pos:
                hold=time.time()-pos["opened_ts"]
                if pos["direction"]=="LONG_GATE_SHORT_OKX":
                    pnl=((abid/pos["a_entry"]-1)+(1-bask/pos["b_entry"]))*10000-fees-EXTRA_BUFFER_BPS
                else:
                    pnl=((1-aask/pos["a_entry"])+(bbid/pos["b_entry"]-1))*10000-fees-EXTRA_BUFFER_BPS
                reason=None
                if abs(z)<=EXIT_Z:reason="CONVERGENCE"
                elif abs(z)>=STOP_Z:reason="Z_STOP"
                elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
                if reason:
                    append(LEDGER_FILE,LEDGER_FIELDS,{
                      "utc":utc(),"event":"CLOSE","direction":pos["direction"],
                      "entry_z":pos["entry_z"],"exit_z":z,"entry_exec_bps":pos["entry_exec_bps"],
                      "expected_net_bps":pos["expected_net_bps"],"realized_net_bps":pnl,
                      "hold_sec":hold,"reason":reason
                    })
                    state["position"]=None
            else:
                if z>=ENTRY_Z and expected_high>0:
                    state["position"]={
                      "direction":"LONG_GATE_SHORT_OKX","opened_ts":time.time(),
                      "entry_z":z,"entry_exec_bps":hi,"expected_net_bps":expected_high,
                      "a_entry":aask,"b_entry":bbid
                    }
                    append(LEDGER_FILE,LEDGER_FIELDS,{
                      "utc":utc(),"event":"OPEN","direction":"LONG_GATE_SHORT_OKX",
                      "entry_z":z,"entry_exec_bps":hi,"expected_net_bps":expected_high,
                      "reason":"LIVE_EXECUTABLE_DISLOCATION"
                    })
                elif z<=-ENTRY_Z and expected_low>0:
                    state["position"]={
                      "direction":"SHORT_GATE_LONG_OKX","opened_ts":time.time(),
                      "entry_z":z,"entry_exec_bps":lo,"expected_net_bps":expected_low,
                      "a_entry":abid,"b_entry":bask
                    }
                    append(LEDGER_FILE,LEDGER_FIELDS,{
                      "utc":utc(),"event":"OPEN","direction":"SHORT_GATE_LONG_OKX",
                      "entry_z":z,"entry_exec_bps":lo,"expected_net_bps":expected_low,
                      "reason":"LIVE_EXECUTABLE_DISLOCATION"
                    })

            row={"utc":utc(),"sample":i,"a_bid":abid,"a_ask":aask,"b_bid":bbid,"b_ask":bask,
                 "mid_basis_bps":x,"z":z,"baseline_mean_bps":mean,"baseline_sd_bps":sd,
                 "exec_high_bps":hi,"exec_low_bps":lo,"fee_bps":fees,
                 "exit_spread_bps":exit_spread,"expected_high_bps":expected_high,
                 "expected_low_bps":expected_low,
                 "position":(state.get("position") or {}).get("direction","FLAT")}
            rows.append(row);append(TS_FILE,FIELDS,row);save_state(state)
        except Exception as e:
            rows.append({"utc":utc(),"sample":i,"error":f"{type(e).__name__}: {e}"})
        if i<SAMPLES-1:time.sleep(SLEEP_SEC)

    report={"utc":utc(),"route":"OP|GATE|OKX","baseline":{"mean":mean,"sd":sd,"bars":nbase},
            "samples":rows,"position":state.get("position"),
            "positive_executable_samples":sum(max(float(r.get("expected_high_bps",-1e9)),
                                                  float(r.get("expected_low_bps",-1e9)))>0
                                              for r in rows if "error" not in r)}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"route":report["route"],"baseline":report["baseline"],
                      "positive_executable_samples":report["positive_executable_samples"],
                      "position":report["position"],
                      "last":rows[-1] if rows else None},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
