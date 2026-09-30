from __future__ import annotations

import csv, json, math, statistics, time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"si_calendar_fast_state.json"
TS_FILE=STATE_DIR/"si_calendar_fast_timeseries.csv"
LEDGER_FILE=STATE_DIR/"si_calendar_fast_ledger.csv"
LAST_FILE=STATE_DIR/"si_calendar_fast_last.json"

BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-SI-CALENDAR-FAST/1.0"})

NEAR="SiZ6"
FAR="SiH7"
HIST_BARS=90
ENTRY_Z=3.0
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=1800
SAMPLES=4
SLEEP_SEC=10
EXTRA_BUFFER_RUB=2.0
MIN_EXPECTED_NET_RUB=5.0

TS_FIELDS=["utc","near_bid","near_ask","far_bid","far_ask","mid_spread","exec_high","exec_low",
           "baseline_mean","baseline_sd","z","cost_rub","expected_high_rub","expected_low_rub",
           "position_status"]
LEDGER_FIELDS=["utc","event","direction","entry_z","exit_z","entry_spread","exit_spread",
               "expected_net_rub","realized_net_rub","hold_sec","reason"]

def utc():return datetime.now(timezone.utc).isoformat()

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=15);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def meta(secid):
    j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s or not m:return None
    a=s[0];b=m[0]
    ms=num(a.get("MINSTEP")) or 1.0
    sp=num(a.get("STEPPRICE")) or ms
    return {"unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0,
            "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
            "trades":num(b.get("NUMTRADES")) or 0.0,
            "systime":b.get("SYSTIME")}

def candles(secid):
    till=datetime.now(timezone.utc)
    frm=till-timedelta(days=2)
    j=get(f"{BASE}/engines/futures/markets/forts/securities/{secid}/candles.json",
          {"from":frm.strftime("%Y-%m-%d"),"till":till.strftime("%Y-%m-%d"),
           "interval":1,"iss.meta":"off"})
    return {r.get("begin"):num(r.get("close")) for r in rows(j,"candles")
            if r.get("begin") and num(r.get("close")) is not None}

def baseline():
    A=candles(NEAR);B=candles(FAR)
    ts=sorted(set(A)&set(B))[-HIST_BARS:]
    xs=[B[t]-A[t] for t in ts]
    if len(xs)<20:return None
    return statistics.fmean(xs),statistics.pstdev(xs),len(xs)

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"position":None}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append_csv(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def snapshot(mean,sd,n_hist,state):
    n=meta(NEAR);f=meta(FAR)
    if not n or not f or None in (n["bid"],n["ask"],f["bid"],f["ask"]):
        return None
    if n["unit"]<=0 or f["unit"]<=0:return None
    qty=n["unit"]/f["unit"]
    mid=((f["bid"]+f["ask"])/2)-((n["bid"]+n["ask"])/2)
    z=(mid-mean)/sd if sd and sd>1e-12 else 0.0
    high=f["bid"]-n["ask"] # long near / short far
    low=f["ask"]-n["bid"]  # short near / long far
    roundtrip_fees=2*(n["fee"]+qty*f["fee"])
    exit_spread=(n["ask"]-n["bid"])*n["unit"]+(f["ask"]-f["bid"])*qty*f["unit"]
    cost=roundtrip_fees+exit_spread+EXTRA_BUFFER_RUB
    expected_high=(high-mean)*n["unit"]-cost
    expected_low=(mean-low)*n["unit"]-cost

    pos=state.get("position")
    status="FLAT" if not pos else pos["direction"]
    row={"utc":utc(),"near_bid":n["bid"],"near_ask":n["ask"],"far_bid":f["bid"],"far_ask":f["ask"],
         "mid_spread":mid,"exec_high":high,"exec_low":low,"baseline_mean":mean,"baseline_sd":sd,
         "z":z,"cost_rub":cost,"expected_high_rub":expected_high,"expected_low_rub":expected_low,
         "position_status":status}

    if pos:
        hold=time.time()-pos["opened_ts"]
        if pos["direction"]=="LONG_NEAR_SHORT_FAR":
            pnl=((n["bid"]-pos["near_entry"])*n["unit"]
                 +(pos["far_entry"]-f["ask"])*qty*f["unit"]
                 -pos["fees_rub"]-EXTRA_BUFFER_RUB)
        else:
            pnl=((pos["near_entry"]-n["ask"])*n["unit"]
                 +(f["bid"]-pos["far_entry"])*qty*f["unit"]
                 -pos["fees_rub"]-EXTRA_BUFFER_RUB)
        reason=None
        if abs(z)<=EXIT_Z:reason="CONVERGENCE"
        elif abs(z)>=STOP_Z:reason="Z_STOP"
        elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
        if reason:
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":"CLOSE","direction":pos["direction"],
                "entry_z":pos["entry_z"],"exit_z":z,"entry_spread":pos["entry_spread"],
                "exit_spread":mid,"expected_net_rub":pos["expected_net_rub"],
                "realized_net_rub":pnl,"hold_sec":hold,"reason":reason})
            state["position"]=None
    else:
        direction=None;expected=None
        if z>=ENTRY_Z and expected_high>=MIN_EXPECTED_NET_RUB:
            direction="LONG_NEAR_SHORT_FAR";expected=expected_high
            near_entry=n["ask"];far_entry=f["bid"];entry_spread=high
        elif z<=-ENTRY_Z and expected_low>=MIN_EXPECTED_NET_RUB:
            direction="SHORT_NEAR_LONG_FAR";expected=expected_low
            near_entry=n["bid"];far_entry=f["ask"];entry_spread=low
        if direction:
            state["position"]={"direction":direction,"opened_ts":time.time(),"entry_z":z,
                               "entry_spread":entry_spread,"near_entry":near_entry,"far_entry":far_entry,
                               "expected_net_rub":expected,"fees_rub":roundtrip_fees}
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":"OPEN","direction":direction,
                "entry_z":z,"entry_spread":entry_spread,"expected_net_rub":expected,
                "reason":"LIVE_BIDASK_DISLOCATION"})
    return row

def main():
    b=baseline()
    if not b:
        raise SystemExit("insufficient 1m baseline")
    mean,sd,n_hist=b
    state=load_state()
    samples=[]
    for i in range(SAMPLES):
        try:
            r=snapshot(mean,sd,n_hist,state)
            if r:
                samples.append(r)
                append_csv(TS_FILE,TS_FIELDS,r)
                save_state(state)
        except Exception as e:
            samples.append({"utc":utc(),"error":f"{type(e).__name__}: {e}"})
        if i<SAMPLES-1:time.sleep(SLEEP_SEC)
    report={"utc":utc(),"baseline":{"mean":mean,"sd":sd,"bars":n_hist},
            "samples":samples,"position":state.get("position")}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"baseline":report["baseline"],"last":samples[-1] if samples else None,
                      "position":state.get("position")},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
