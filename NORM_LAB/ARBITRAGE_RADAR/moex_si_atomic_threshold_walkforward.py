from __future__ import annotations

import json, math, statistics
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-SI-ATOMIC-WF/1.0"})

NEAR="SiZ6"
FAR="SiH7"
DAYS=30
LOOKBACK=60
THRESHOLDS=[1.5,1.75,2.0,2.25,2.5,2.75,3.0]
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_BARS=120
EXTRA_RUB=2.0
STRESS=[0.0,5.0,10.0]

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=30);r.raise_for_status();return r.json()

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
    if not s:return None
    a=s[0];b=m[0] if m else {}
    ms=num(a.get("MINSTEP")) or 1.0
    sp=num(a.get("STEPPRICE")) or ms
    return {"unit":sp/ms,"scalper_fee":num(a.get("SCALPERFEE")) or 0.0,
            "buy_sell_fee":num(a.get("BUYSELLFEE")) or 0.0,
            "bid":num(b.get("BID")),"ask":num(b.get("OFFER"))}

def candles(secid):
    till=datetime.now(timezone.utc);frm=till-timedelta(days=DAYS)
    url=f"{BASE}/engines/futures/markets/forts/securities/{secid}/candles.json"
    out=[];start=0
    while True:
        j=get(url,{"from":frm.strftime("%Y-%m-%d"),"till":till.strftime("%Y-%m-%d"),
                   "interval":1,"start":start,"iss.meta":"off"})
        rr=rows(j,"candles")
        if not rr:break
        out.extend(rr)
        if len(rr)<500:break
        start+=len(rr)
        if start>30000:break
    d={}
    for r in out:
        t=r.get("begin");c=num(r.get("close"));v=num(r.get("volume"))
        if t and c is not None:d[t]={"close":c,"volume":v or 0.0}
    return d

def generate_series():
    A=candles(NEAR);B=candles(FAR)
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0]
    return ts,{t:B[t]["close"]-A[t]["close"] for t in ts}

def simulate(ts,X,threshold,cost,unit):
    hist=deque(maxlen=LOOKBACK);pos=None;tr=[]
    for t in ts:
        x=X[t]
        if len(hist)<LOOKBACK:
            hist.append(x);continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist)
        z=(x-mean)/sd if sd>1e-12 else 0.0
        if pos:
            pos["bars"]+=1
            pnl=((pos["entry_x"]-x) if pos["dir"]=="HIGH" else (x-pos["entry_x"]))*unit-cost
            reason=None
            if abs(z)<=EXIT_Z:reason="CONVERGENCE"
            elif abs(z)>=STOP_Z:reason="Z_STOP"
            elif pos["bars"]>=MAX_HOLD_BARS:reason="MAX_HOLD"
            if reason:
                tr.append({**pos,"exit":t,"exit_z":z,"net_rub":pnl,"reason":reason})
                pos=None
        else:
            if z>=threshold:
                exp=(x-mean)*unit-cost
                if exp>0:pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"HIGH","expected_net_rub":exp,"bars":0}
            elif z<=-threshold:
                exp=(mean-x)*unit-cost
                if exp>0:pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"LOW","expected_net_rub":exp,"bars":0}
        hist.append(x)
    return tr

def metrics(xs):
    if not xs:return None
    ns=[x["net_rub"] for x in xs]
    return {"n":len(ns),"positive_pct":100*sum(x>0 for x in ns)/len(ns),
            "median_net_rub":statistics.median(ns),"mean_net_rub":statistics.fmean(ns),
            "aggregate_net_rub":sum(ns),"worst_net_rub":min(ns),"best_net_rub":max(ns)}

def score(m):
    if not m or m["n"]<8 or m["positive_pct"]<60 or m["median_net_rub"]<=0:return -1e99
    return m["median_net_rub"]*math.sqrt(m["n"])*(m["positive_pct"]/100)

def main():
    nm=meta(NEAR);fm=meta(FAR)
    if not nm or not fm:raise RuntimeError("metadata missing")
    unit=nm["unit"]
    fqty=nm["unit"]/fm["unit"]
    # Conservative current production proxy: full BUYSELLFEE of both legs on
    # entry and exit. Do not rely on expired marketing/scalper discounts.
    atomic_roundtrip_fee=2*(nm["buy_sell_fee"]+fqty*fm["buy_sell_fee"])
    atomic_width_proxy=1.0*unit
    base_cost=atomic_roundtrip_fee+atomic_width_proxy+EXTRA_RUB

    ts,X=generate_series()
    cutoff_idx=int(len(ts)*0.70)
    cutoff=ts[cutoff_idx] if ts else None
    train_ts=ts[:cutoff_idx]
    hold_ts=ts[cutoff_idx:]

    selections=[]
    for extra in STRESS:
        cost=base_cost+extra
        candidates=[]
        for th in THRESHOLDS:
            tr=simulate(train_ts,{t:X[t] for t in train_ts},th,cost,unit)
            m=metrics(tr)
            candidates.append({"threshold":th,"train":m,"score":score(m)})
        candidates.sort(key=lambda r:r["score"],reverse=True)
        best=candidates[0]
        th=best["threshold"]
        htr=simulate(hold_ts,{t:X[t] for t in hold_ts},th,cost,unit)
        selections.append({"extra_stress_rub":extra,"cost_rub":cost,
                           "selected_threshold":th,"train":best["train"],
                           "train_score":best["score"],"holdout":metrics(htr),
                           "all_thresholds":candidates})
    report={"near":NEAR,"far":FAR,"days_requested":DAYS,"bars":len(ts),
            "cutoff":cutoff,"atomic_cost_proxy_rub":base_cost,
            "selection":selections}
    (OUT/"moex_si_atomic_threshold_walkforward.json").write_text(
      json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
