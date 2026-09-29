from __future__ import annotations

import csv, json, statistics, time
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

VENUES=["okx","bitget","gate","mexc","htx"]
ASSETS=["OP","ARB","ICP","WIF","ETH","BTC","DOGE","SOL"]
FEE={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008,"HTX":0.0005}
THRESHOLDS=[0.0,5.0,10.0,15.0,20.0]
DAYS=21
EXEC_BUFFER_BPS=4.0

def build(exid):
 ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
 ex.load_markets()
 return exid.upper(),ex

def sym(ex,base):
 ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear") and
     m.get("active",True) and m.get("base")==base and m.get("quote")=="USDT"]
 return ms[0]["symbol"] if ms else None

def funding(ex,symbol,since):
 if not ex.has.get("fetchFundingRateHistory"):return {}
 out=[];cursor=since
 for _ in range(10):
  try:rr=ex.fetch_funding_rate_history(symbol,cursor,200)
  except Exception:break
  if not rr:break
  out.extend(rr)
  mx=max(int(x.get("timestamp") or 0) for x in rr)
  if mx<=cursor:break
  cursor=mx+1
  if cursor>=int(time.time()*1000)-60000:break
 d={}
 for x in out:
  t=int(x.get("timestamp") or 0);r=x.get("fundingRate")
  if t and r is not None:d[t]=float(r)
 return d

def ohlcv(ex,symbol,since):
 out=[];cursor=since
 for _ in range(20):
  try:rr=ex.fetch_ohlcv(symbol,"1h",cursor,500)
  except Exception:break
  if not rr:break
  out.extend(rr)
  mx=max(int(x[0]) for x in rr)
  if mx<=cursor:break
  cursor=mx+3600000
  if cursor>=int(time.time()*1000)-3600000:break
 return {int(x[0]):float(x[4]) for x in out if len(x)>=5}

def px(series,t):
 if t in series:return series[t]
 for d in (3600000,7200000):
  if t-d in series:return series[t-d]
 return None

def main():
 since=int((time.time()-DAYS*86400)*1000)
 exs={}
 for x in VENUES:
  try:
   name,ex=build(x);exs[name]=ex
  except Exception:pass

 cache={}
 errors=[]
 for name,ex in exs.items():
  for base in ASSETS:
   s=sym(ex,base)
   if not s:continue
   try:
    cache[(name,base)]={"symbol":s,"fund":funding(ex,s,since),"px":ohlcv(ex,s,since)}
   except Exception as e:
    errors.append({"venue":name,"base":base,"error":f"{type(e).__name__}: {e}"[:500]})

 trials=[]
 names=list(exs)
 for base in ASSETS:
  for ln in names:
   for sn in names:
    if ln==sn:continue
    L=cache.get((ln,base));S=cache.get((sn,base))
    if not L or not S:continue
    events=sorted(set(L["fund"]) & set(S["fund"]))
    if len(events)<3:continue
    rt=2*(FEE.get(ln,.0007)+FEE.get(sn,.0007))*10000+EXEC_BUFFER_BPS
    for i in range(1,len(events)):
     t=events[i];prev=events[i-1]
     # Only compare venues whose funding events are truly synchronized.
     if t-prev>12*3600000:continue
     entry_t=t-3600000
     exit_t=t+3600000
     lp0=px(L["px"],entry_t);sp0=px(S["px"],entry_t)
     lp1=px(L["px"],exit_t);sp1=px(S["px"],exit_t)
     if None in (lp0,sp0,lp1,sp1):continue

     basis_entry=(sp0/lp0-1.0)*10000
     trailing_fund=(-L["fund"][prev]+S["fund"][prev])*10000
     predicted_screen=basis_entry+trailing_fund-rt

     actual_fund=(-L["fund"][t]+S["fund"][t])*10000
     basis_pnl=((lp1/lp0-1.0)+(1.0-sp1/sp0))*10000
     realized=basis_pnl+actual_fund-rt

     trials.append({
      "base":base,"long_provider":ln,"short_provider":sn,
      "funding_ts":t,"entry_ts":entry_t,"exit_ts":exit_t,
      "basis_entry_bps":basis_entry,"trailing_funding_capture_bps":trailing_fund,
      "predicted_screen_bps":predicted_screen,
      "actual_funding_capture_bps":actual_fund,
      "basis_pnl_bps":basis_pnl,"fees_execution_bps":rt,
      "realized_net_bps":realized
     })

 if trials:
  with (OUT/"crypto_event_signal_trials.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(trials[0].keys()));w.writeheader();w.writerows(trials)

 summary=[]
 routes=sorted(set((r["base"],r["long_provider"],r["short_provider"]) for r in trials))
 for route in routes:
  rr=[r for r in trials if (r["base"],r["long_provider"],r["short_provider"])==route]
  for th in THRESHOLDS:
   xs=[r for r in rr if r["predicted_screen_bps"]>=th and r["trailing_funding_capture_bps"]>0]
   if not xs:continue
   nets=[r["realized_net_bps"] for r in xs]
   summary.append({
    "base":route[0],"long_provider":route[1],"short_provider":route[2],
    "entry_threshold_bps":th,"n":len(xs),
    "positive_pct":100*sum(x>0 for x in nets)/len(nets),
    "median_realized_net_bps":statistics.median(nets),
    "mean_realized_net_bps":statistics.fmean(nets),
    "aggregate_realized_net_bps":sum(nets),
    "worst_net_bps":min(nets),"best_net_bps":max(nets)
   })
 summary.sort(key=lambda x:(x["median_realized_net_bps"],x["n"]),reverse=True)
 (OUT/"crypto_event_signal_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
 (OUT/"crypto_event_signal_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(summary[:50],ensure_ascii=False,indent=2))

if __name__=="__main__":main()
