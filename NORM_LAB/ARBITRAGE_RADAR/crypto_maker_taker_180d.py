from __future__ import annotations

import json, math, statistics, time
from collections import deque
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)

VENUES=["okx","bitget","gate","mexc"]
ASSETS=["BTC","ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH",
        "SUI","HBAR","WIF","APT","ARB","OP","UNI","DOT","FIL","ICP","AAVE","ETC"]

MAKER={"OKX":0.0002,"BITGET":0.0002,"GATE":0.0002,"MEXC":0.0006}
TAKER={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008}

DAYS=180
LOOKBACK=72
ENTRY_ZS=[2.5,3.0,3.5]
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_H=12
EXTRA_BUFFER_BPS=8.0
TRAIN_FRAC=0.70
MIN_TRAIN=5

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    return exid.upper(),ex

def symbol(ex,base):
    ms=[m for m in ex.markets.values()
        if m.get("swap") and m.get("linear") and m.get("active",True)
        and m.get("base")==base and m.get("quote")=="USDT"]
    return ms[0]["symbol"] if ms else None

def history(ex,sym,since):
    out=[];cursor=since
    for _ in range(16):
        try:rr=ex.fetch_ohlcv(sym,"1h",cursor,500)
        except Exception:break
        if not rr:break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor:break
        cursor=mx+3600000
        if cursor>=int(time.time()*1000)-3600000:break
    return {int(x[0]):{"close":float(x[4]),"volume":float(x[5] or 0)}
            for x in out if len(x)>=6}

def simulate(A,B,cost_bps,entry_z):
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
                raw=((A[t]["close"]/pos["a0"]-1)+(1-B[t]["close"]/pos["b0"]))*10000
            else:
                raw=((1-A[t]["close"]/pos["a0"])+(B[t]["close"]/pos["b0"]-1))*10000
            net=raw-cost_bps
            reason=None
            if abs(z)<=EXIT_Z:reason="CONVERGENCE"
            elif abs(z)>=STOP_Z:reason="Z_STOP"
            elif pos["bars"]>=MAX_HOLD_H:reason="MAX_HOLD"
            if reason:
                tr.append({**pos,"exit_ts":t,"exit_z":z,"net_bps":net,"reason":reason})
                pos=None
        else:
            if z>=entry_z:
                expected=(x-mean)-cost_bps
                if expected>0:
                    pos={"entry_ts":t,"entry_z":z,"dir":"HIGH",
                         "a0":A[t]["close"],"b0":B[t]["close"],
                         "expected_net_bps":expected,"bars":0}
            elif z<=-entry_z:
                expected=(mean-x)-cost_bps
                if expected>0:
                    pos={"entry_ts":t,"entry_z":z,"dir":"LOW",
                         "a0":A[t]["close"],"b0":B[t]["close"],
                         "expected_net_bps":expected,"bars":0}
        hist.append(x)
    return tr,len(ts)

def metrics(xs):
    if not xs:return None
    v=[x["net_bps"] for x in xs]
    return {"n":len(v),"positive_pct":100*sum(x>0 for x in v)/len(v),
            "median_net_bps":statistics.median(v),"mean_net_bps":statistics.fmean(v),
            "aggregate_net_bps":sum(v),"worst_net_bps":min(v),"best_net_bps":max(v)}

def main():
    since=int((time.time()-DAYS*86400)*1000)
    exs={};cache={};errors=[]
    for exid in VENUES:
        try:name,ex=build(exid);exs[name]=ex
        except Exception as e:errors.append({"venue":exid.upper(),"error":f"{type(e).__name__}: {e}"[:500]})
    for name,ex in exs.items():
        for base in ASSETS:
            s=symbol(ex,base)
            if not s:continue
            try:cache[(name,base)]=history(ex,s,since)
            except Exception as e:errors.append({"venue":name,"base":base,"error":f"{type(e).__name__}: {e}"[:500]})

    allres=[];names=sorted(exs)
    for base in ASSETS:
        for maker in names:
            for hedge in names:
                if maker==hedge:continue
                A=cache.get((maker,base));B=cache.get((hedge,base))
                if not A or not B:continue
                cost=2*(MAKER[maker]+TAKER[hedge])*10000+EXTRA_BUFFER_BPS
                route_events=[]
                for ez in ENTRY_ZS:
                    tr,bars=simulate(A,B,cost,ez)
                    if not tr:continue
                    times=sorted(set(x["entry_ts"] for x in tr))
                    if len(times)<2:continue
                    cutoff=times[max(0,min(len(times)-1,int(len(times)*TRAIN_FRAC)))]
                    train=[x for x in tr if x["entry_ts"]<cutoff]
                    hold=[x for x in tr if x["entry_ts"]>=cutoff]
                    tm=metrics(train);hm=metrics(hold)
                    if not tm or tm["n"]<MIN_TRAIN:continue
                    score=tm["median_net_bps"]*math.sqrt(tm["n"])*(tm["positive_pct"]/100)
                    route_events.append({
                      "base":base,"maker_venue":maker,"hedge_venue":hedge,
                      "entry_z":ez,"cost_bps":cost,"bars":bars,
                      "train_score":score,"train":tm,"holdout":hm
                    })
                if route_events:
                    route_events.sort(key=lambda x:x["train_score"],reverse=True)
                    sel=route_events[0]
                    hm=sel.get("holdout")
                    sel["holdout_pass"]=bool(hm and hm["n"]>=2 and hm["positive_pct"]>=60
                                             and hm["median_net_bps"]>0 and hm["aggregate_net_bps"]>0)
                    allres.append(sel)

    allres.sort(key=lambda r:(1 if r["holdout_pass"] else 0,
                              (r.get("holdout") or {}).get("aggregate_net_bps",-1e99),
                              r["train_score"]),reverse=True)
    (OUT/"crypto_maker_taker_180d.json").write_text(json.dumps(allres,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"crypto_maker_taker_180d_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"routes":len(allres),
                      "holdout_pass":sum(r["holdout_pass"] for r in allres),
                      "top":allres[:40]},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
