from __future__ import annotations

import json, math, statistics
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-SI-ROBUSTNESS/1.0"})
NEAR="SiZ6";FAR="SiH7"
DAYS=30
LOOKBACKS=[30,60,120]
ENTRIES=[2.5,3.0,3.5]
EXITS=[0.25,0.5,1.0]
MAX_HOLDS=[30,60,120]
STOP_Z=5.0
EXTRA_STRESS_RUB=10.0

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
    ms=num(a.get("MINSTEP")) or 1.0;sp=num(a.get("STEPPRICE")) or ms
    return {"unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0,
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
    return {r["begin"]:{"close":float(r["close"]),"volume":float(r.get("volume") or 0)}
            for r in out if r.get("begin") and r.get("close") is not None}

def simulate(ts,X,lb,entry,exit_z,maxhold,cost,unit):
    hist=deque(maxlen=lb);pos=None;tr=[]
    for t in ts:
        x=X[t]
        if len(hist)<lb:
            hist.append(x);continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist)
        z=(x-mean)/sd if sd>1e-12 else 0
        if pos:
            pos["bars"]+=1
            pnl=((pos["entry_x"]-x) if pos["dir"]=="HIGH" else (x-pos["entry_x"]))*unit-cost
            reason=None
            if abs(z)<=exit_z:reason="CONVERGENCE"
            elif abs(z)>=STOP_Z:reason="Z_STOP"
            elif pos["bars"]>=maxhold:reason="MAX_HOLD"
            if reason:
                tr.append({**pos,"exit":t,"exit_z":z,"net_rub":pnl,"reason":reason});pos=None
        else:
            if z>=entry:
                exp=(x-mean)*unit-cost
                if exp>0:pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"HIGH","bars":0}
            elif z<=-entry:
                exp=(mean-x)*unit-cost
                if exp>0:pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"LOW","bars":0}
        hist.append(x)
    return tr

def metrics(xs):
    if not xs:return None
    ns=[x["net_rub"] for x in xs]
    return {"n":len(ns),"positive_pct":100*sum(x>0 for x in ns)/len(ns),
            "median":statistics.median(ns),"mean":statistics.fmean(ns),
            "aggregate":sum(ns),"worst":min(ns),"best":max(ns)}

def main():
    nm=meta(NEAR);fm=meta(FAR)
    if not nm or not fm:raise RuntimeError("metadata missing")
    unit=nm["unit"];fqty=unit/fm["unit"]
    width=0.0
    if None not in (nm["bid"],nm["ask"],fm["bid"],fm["ask"]):
        width=(nm["ask"]-nm["bid"])*unit+(fm["ask"]-fm["bid"])*fqty*fm["unit"]
    # full fees + current synthetic width + extra stress
    cost=2*(nm["fee"]+fqty*fm["fee"])+width+EXTRA_STRESS_RUB
    A=candles(NEAR);B=candles(FAR)
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0]
    X={t:B[t]["close"]-A[t]["close"] for t in ts}
    cut=int(len(ts)*0.70);train_ts=ts[:cut];hold_ts=ts[cut:]

    combos=[]
    for lb in LOOKBACKS:
      for en in ENTRIES:
       for ex in EXITS:
        for mh in MAX_HOLDS:
         tr=simulate(train_ts,X,lb,en,ex,mh,cost,unit)
         ho=simulate(hold_ts,X,lb,en,ex,mh,cost,unit)
         tm=metrics(tr);hm=metrics(ho)
         robust=bool(hm and hm["n"]>=8 and hm["positive_pct"]>=65 and hm["median"]>0 and hm["aggregate"]>0)
         combos.append({"lookback":lb,"entry_z":en,"exit_z":ex,"max_hold_min":mh,
                        "train":tm,"holdout":hm,"holdout_pass":robust})
    passes=[x for x in combos if x["holdout_pass"]]
    report={
      "days":DAYS,"bars":len(ts),"cost_rub":cost,"extra_stress_rub":EXTRA_STRESS_RUB,
      "grid_size":len(combos),"holdout_pass_count":len(passes),
      "holdout_pass_pct":100*len(passes)/len(combos),
      "parameter_pass_map":{
        "lookback":{str(v):sum(x["holdout_pass"] and x["lookback"]==v for x in combos) for v in LOOKBACKS},
        "entry_z":{str(v):sum(x["holdout_pass"] and x["entry_z"]==v for x in combos) for v in ENTRIES},
        "exit_z":{str(v):sum(x["holdout_pass"] and x["exit_z"]==v for x in combos) for v in EXITS},
        "max_hold":{str(v):sum(x["holdout_pass"] and x["max_hold_min"]==v for x in combos) for v in MAX_HOLDS},
      },
      "top_holdout":sorted(passes,key=lambda x:(x["holdout"]["aggregate"],x["holdout"]["median"]),reverse=True)[:30],
      "all":combos
    }
    (OUT/"moex_si_robustness_grid.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("all","top_holdout")}|{"top":report["top_holdout"][:10]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
