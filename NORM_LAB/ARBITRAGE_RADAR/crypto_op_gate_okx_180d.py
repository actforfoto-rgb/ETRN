from __future__ import annotations

import json, math, statistics, time
from collections import deque
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)

BASE="OP"
VENUE_A="gate"
VENUE_B="okx"
ENTRY_Z=3.0
LOOKBACK=48
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_H=12
DAYS=180
FEE_A=0.0005
FEE_B=0.0005
BASE_COST_BPS=2*(FEE_A+FEE_B)*10000+8.0
STRESS_EXTRA=[0.0,5.0,10.0,15.0,20.0,25.0,30.0]

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear") and
        m.get("active",True) and m.get("base")==BASE and m.get("quote")=="USDT"]
    if not ms:raise RuntimeError(f"{exid}: {BASE} USDT linear swap missing")
    return ex,ms[0]["symbol"]

def fetch(ex,sym,since):
    out=[];cursor=since
    for _ in range(20):
        rr=ex.fetch_ohlcv(sym,"1h",cursor,500)
        if not rr:break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor:break
        cursor=mx+3600000
        if cursor>=int(time.time()*1000)-3600000:break
    d={}
    for x in out:
        if len(x)<6:continue
        d[int(x[0])]={"close":float(x[4]),"volume":float(x[5] or 0)}
    return d

def simulate(A,B,cost):
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0]
    hist=deque(maxlen=LOOKBACK);pos=None;tr=[]
    for t in ts:
        x=(B[t]["close"]/A[t]["close"]-1)*10000
        if len(hist)<LOOKBACK:
            hist.append(x);continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist)
        z=(x-mean)/sd if sd>1e-12 else 0.0
        if pos:
            pos["bars"]+=1
            if pos["dir"]=="HIGH":
                pnl=((A[t]["close"]/pos["a0"]-1)+(1-B[t]["close"]/pos["b0"]))*10000-cost
            else:
                pnl=((1-A[t]["close"]/pos["a0"])+(B[t]["close"]/pos["b0"]-1))*10000-cost
            reason=None
            if abs(z)<=EXIT_Z:reason="CONVERGENCE"
            elif abs(z)>=STOP_Z:reason="Z_STOP"
            elif pos["bars"]>=MAX_HOLD_H:reason="MAX_HOLD"
            if reason:
                tr.append({**pos,"exit_ts":t,"exit_z":z,"net_bps":pnl,"reason":reason})
                pos=None
        else:
            if z>=ENTRY_Z:
                exp=(x-mean)-cost
                if exp>0:
                    pos={"entry_ts":t,"entry_z":z,"dir":"HIGH",
                         "a0":A[t]["close"],"b0":B[t]["close"],
                         "expected_net_bps":exp,"bars":0}
            elif z<=-ENTRY_Z:
                exp=(mean-x)-cost
                if exp>0:
                    pos={"entry_ts":t,"entry_z":z,"dir":"LOW",
                         "a0":A[t]["close"],"b0":B[t]["close"],
                         "expected_net_bps":exp,"bars":0}
        hist.append(x)
    return tr,ts

def metrics(xs):
    if not xs:return None
    nets=[x["net_bps"] for x in xs]
    return {
      "n":len(xs),
      "positive_pct":100*sum(x>0 for x in nets)/len(nets),
      "median_net_bps":statistics.median(nets),
      "mean_net_bps":statistics.fmean(nets),
      "aggregate_net_bps":sum(nets),
      "worst_net_bps":min(nets),"best_net_bps":max(nets)
    }

def main():
    a,sa=build(VENUE_A);b,sb=build(VENUE_B)
    since=int((time.time()-DAYS*86400)*1000)
    A=fetch(a,sa,since);B=fetch(b,sb,since)
    report={"base":BASE,"venue_a":"GATE","venue_b":"OKX","entry_z":ENTRY_Z,
            "days_requested":DAYS,"base_cost_bps":BASE_COST_BPS,"stress":[]}
    alltr={}
    for extra in STRESS_EXTRA:
        cost=BASE_COST_BPS+extra
        tr,ts=simulate(A,B,cost)
        cutoff=ts[int(len(ts)*0.70)] if ts else None
        train=[x for x in tr if cutoff and x["entry_ts"]<cutoff]
        hold=[x for x in tr if cutoff and x["entry_ts"]>=cutoff]
        block={"extra_stress_bps":extra,"cost_bps":cost,"bars":len(ts),
               "cutoff_ts":cutoff,"train":metrics(train),"holdout":metrics(hold),
               "full":metrics(tr)}
        report["stress"].append(block)
        alltr[str(extra)]=tr
    (OUT/"crypto_op_gate_okx_180d.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"crypto_op_gate_okx_180d_trades.json").write_text(json.dumps(alltr,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
