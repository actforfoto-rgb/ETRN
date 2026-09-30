from __future__ import annotations

import json, statistics, time
from collections import deque
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)

VENUES=["okx","bitget","gate","mexc"]
ASSETS=["BTC","ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH",
        "SUI","HBAR","WIF","APT","ARB","OP","UNI","DOT","FIL","ICP"]
FEE={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008}

DAYS=60
LOOKBACK=48
ENTRY_ZS=[2.0,2.5,3.0]
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_H=12
EXEC_BUFFER_BPS=8.0

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    return exid.upper(),ex

def symbol(ex,base):
    ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
        and m.get("active",True) and m.get("base")==base and m.get("quote")=="USDT"]
    return ms[0]["symbol"] if ms else None

def ohlcv(ex,sym,since):
    out=[];cursor=since
    for _ in range(12):
        try:rr=ex.fetch_ohlcv(sym,"1h",cursor,500)
        except Exception:break
        if not rr:break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor:break
        cursor=mx+3600000
        if cursor>=int(time.time()*1000)-3600000:break
    return {int(x[0]):{"close":float(x[4]),"volume":float(x[5] or 0.0)}
         for x in out if len(x)>=6}

def simulate(A,B,cost,ez):
    ts=[t for t in sorted(set(A)&set(B))
     if float(A[t].get("volume") or 0)>0 and float(B[t].get("volume") or 0)>0];hist=deque(maxlen=LOOKBACK);pos=None;tr=[]
    for t in ts:
        x=(B[t]["close"]/A[t]["close"]-1)*10000
        if len(hist)<LOOKBACK:
            hist.append(x);continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist);z=(x-mean)/sd if sd>1e-12 else 0
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
                tr.append({**pos,"exit_ts":t,"exit_z":z,"net_bps":pnl,"reason":reason});pos=None
        else:
            if z>=ez:
                expected=(x-mean)-cost
                if expected>0:pos={"entry_ts":t,"entry_z":z,"dir":"HIGH","a0":A[t]["close"],"b0":B[t]["close"],"expected_net_bps":expected,"bars":0}
            elif z<=-ez:
                expected=(mean-x)-cost
                if expected>0:pos={"entry_ts":t,"entry_z":z,"dir":"LOW","a0":A[t]["close"],"b0":B[t]["close"],"expected_net_bps":expected,"bars":0}
        hist.append(x)
    return tr,len(ts)

def main():
    since=int((time.time()-DAYS*86400)*1000); exs={};cache={};errors=[]
    for exid in VENUES:
        try:name,ex=build(exid);exs[name]=ex
        except Exception as e:errors.append({"venue":exid.upper(),"error":f"{type(e).__name__}: {e}"[:500]})
    for name,ex in exs.items():
        for base in ASSETS:
            s=symbol(ex,base)
            if not s:continue
            try:cache[(name,base)]=ohlcv(ex,s,since)
            except Exception as e:errors.append({"venue":name,"base":base,"error":f"{type(e).__name__}: {e}"[:500]})

    summary=[];trades=[];names=sorted(exs)
    for base in ASSETS:
        for i in range(len(names)):
            for j in range(i+1,len(names)):
                a,b=names[i],names[j];A=cache.get((a,base));B=cache.get((b,base))
                if not A or not B:continue
                cost=2*(FEE[a]+FEE[b])*10000+EXEC_BUFFER_BPS
                for ez in ENTRY_ZS:
                    tr,n=simulate(A,B,cost,ez)
                    for x in tr:x.update({"base":base,"venue_a":a,"venue_b":b,"entry_z_threshold":ez})
                    trades.extend(tr)
                    if not tr:continue
                    nets=[x["net_bps"] for x in tr]
                    summary.append({
                        "base":base,"venue_a":a,"venue_b":b,"entry_z":ez,
                        "bars":n,"trades":len(tr),
                        "positive_pct":100*sum(x>0 for x in nets)/len(nets),
                        "median_net_bps":statistics.median(nets),
                        "mean_net_bps":statistics.fmean(nets),
                        "aggregate_net_bps":sum(nets),
                        "worst_net_bps":min(nets),"best_net_bps":max(nets),
                        "cost_bps":cost
                    })
    summary.sort(key=lambda r:(r["aggregate_net_bps"],r["trades"]),reverse=True)
    (OUT/"crypto_tight_venues_60d_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"crypto_tight_venues_60d_trades.json").write_text(json.dumps(trades,ensure_ascii=False),encoding="utf-8")
    (OUT/"crypto_tight_venues_60d_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary[:50],ensure_ascii=False,indent=2))

if __name__=="__main__":main()
