from __future__ import annotations

import json, statistics
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-SI-30D-EVENT/1.0"})

NEAR="SiZ6"
FAR="SiH7"
DAYS=30
LOOKBACK=60
ENTRY_Z=2.5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_BARS=120
STRESS_EXTRA_RUB=[0.0,5.0,10.0,20.0]

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
    return {"unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0,
            "scalper_fee":num(a.get("SCALPERFEE")) or 0.0,
            "bid":num(b.get("BID")),"ask":num(b.get("OFFER"))}

def candles(secid):
    till=datetime.now(timezone.utc)
    frm=till-timedelta(days=DAYS)
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
        t=r.get("begin")
        c=num(r.get("close"));v=num(r.get("volume"))
        if t and c is not None:d[t]={"close":c,"volume":v or 0.0}
    return d

def simulate(A,B,cost,unit):
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0]
    hist=deque(maxlen=LOOKBACK);pos=None;tr=[]
    for t in ts:
        x=B[t]["close"]-A[t]["close"]
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
                tr.append({**pos,"exit":t,"exit_x":x,"exit_z":z,"net_rub":pnl,"reason":reason})
                pos=None
        else:
            if z>=ENTRY_Z:
                exp=(x-mean)*unit-cost
                if exp>0:pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"HIGH","expected_net_rub":exp,"bars":0}
            elif z<=-ENTRY_Z:
                exp=(mean-x)*unit-cost
                if exp>0:pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"LOW","expected_net_rub":exp,"bars":0}
        hist.append(x)
    return tr,ts

def metrics(xs):
    if not xs:return None
    n=[x["net_rub"] for x in xs]
    return {"n":len(xs),"positive_pct":100*sum(x>0 for x in n)/len(n),
            "median_net_rub":statistics.median(n),"mean_net_rub":statistics.fmean(n),
            "aggregate_net_rub":sum(n),"worst_net_rub":min(n),"best_net_rub":max(n)}

def main():
    nm=meta(NEAR);fm=meta(FAR)
    if not nm or not fm:raise RuntimeError("metadata missing")
    unit=nm["unit"]
    fqty=nm["unit"]/fm["unit"]
    current_spread=0.0
    if None not in (nm["bid"],nm["ask"],fm["bid"],fm["ask"]):
        current_spread=(nm["ask"]-nm["bid"])*nm["unit"]+(fm["ask"]-fm["bid"])*fqty*fm["unit"]
    fees=2*(nm["fee"]+fqty*fm["fee"])
    base_cost=fees+current_spread+2.0

    A=candles(NEAR);B=candles(FAR)
    report={"near":NEAR,"far":FAR,"days_requested":DAYS,"unit_rub":unit,
            "base_cost_proxy_rub":base_cost,"stress":[]}
    trall={}
    for extra in STRESS_EXTRA_RUB:
        cost=base_cost+extra
        tr,ts=simulate(A,B,cost,unit)
        cutoff=ts[int(len(ts)*0.70)] if ts else None
        train=[x for x in tr if cutoff and x["entry"]<cutoff]
        hold=[x for x in tr if cutoff and x["entry"]>=cutoff]
        report["stress"].append({"extra_stress_rub":extra,"cost_rub":cost,"bars":len(ts),
          "cutoff":cutoff,"train":metrics(train),"holdout":metrics(hold),"full":metrics(tr)})
        trall[str(extra)]=tr

    (OUT/"moex_si_calendar_30d.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"moex_si_calendar_30d_trades.json").write_text(json.dumps(trall,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
