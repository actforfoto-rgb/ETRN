from __future__ import annotations

import csv, json, statistics, time
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

ROUTES=[
 {"base":"OP","long":"okx","short":"htx"},
 {"base":"OP","long":"mexc","short":"htx"},
 {"base":"OP","long":"gate","short":"htx"},
 {"base":"ARB","long":"okx","short":"htx"},
 {"base":"ARB","long":"mexc","short":"htx"},
]
FEE={"OKX":0.0005,"MEXC":0.0008,"GATE":0.0005,"HTX":0.0005}
HOLDS_H=[8,24,72,168]
DAYS=14
EXECUTION_BUFFER_BPS=4.0

def build(exid):
 ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
 ex.load_markets()
 return exid.upper(),ex

def symbol(ex,base):
 ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear") and
     m.get("active",True) and m.get("base")==base and m.get("quote")=="USDT"]
 if not ms:raise RuntimeError(f"no {base} USDT linear swap")
 return ms[0]["symbol"]

def funding_hist(ex,sym,since):
 if not ex.has.get("fetchFundingRateHistory"):return {}
 out=[];cursor=since
 for _ in range(10):
  rr=ex.fetch_funding_rate_history(sym,cursor,200)
  if not rr:break
  out.extend(rr)
  mx=max(int(x.get("timestamp") or 0) for x in rr)
  if mx<=cursor:break
  cursor=mx+1
  if cursor>=int(time.time()*1000)-60000:break
 d={}
 for x in out:
  t=int(x.get("timestamp") or 0); r=x.get("fundingRate")
  if t and r is not None:d[t]=float(r)
 return d

def ohlcv(ex,sym,since):
 out=[];cursor=since
 for _ in range(20):
  rr=ex.fetch_ohlcv(sym,"1h",cursor,500)
  if not rr:break
  out.extend(rr)
  mx=max(int(x[0]) for x in rr)
  if mx<=cursor:break
  cursor=mx+3600000
  if cursor>=int(time.time()*1000)-3600000:break
 # ts -> close
 return {int(x[0]):float(x[4]) for x in out if x and len(x)>=5}

def price_at(series,t):
 # exact hour preferred, then nearest earlier hour within 2h
 if t in series:return series[t]
 for dt in (3600000,7200000):
  if t-dt in series:return series[t-dt]
 return None

def sum_funding(hist,start,end):
 return sum(r for t,r in hist.items() if start < t <= end)

def main():
 since=int((time.time()-DAYS*86400)*1000)
 exs={}
 for exid in sorted(set([r["long"] for r in ROUTES]+[r["short"] for r in ROUTES])):
  exs[exid]=build(exid)

 results=[]; errors=[]
 cache={}
 for route in ROUTES:
  try:
   lname,lex=exs[route["long"]]; sname,sex=exs[route["short"]]
   ls=symbol(lex,route["base"]); ss=symbol(sex,route["base"])
   for name,ex,sym in [(lname,lex,ls),(sname,sex,ss)]:
    k=(name,route["base"])
    if k not in cache:
     cache[k]={"px":ohlcv(ex,sym,since),"fund":funding_hist(ex,sym,since),"symbol":sym}
   L=cache[(lname,route["base"])]; S=cache[(sname,route["base"])]
   # enter immediately after a common scheduled funding timestamp that exists in both histories
   entries=sorted(set(L["fund"]) & set(S["fund"]))
   rt=2*(FEE[lname]+FEE[sname])*10000 + EXECUTION_BUFFER_BPS
   for start in entries:
    lp0=price_at(L["px"],start); sp0=price_at(S["px"],start)
    if lp0 is None or sp0 is None:continue
    for h in HOLDS_H:
     end=start+h*3600000
     lp1=price_at(L["px"],end); sp1=price_at(S["px"],end)
     if lp1 is None or sp1 is None:continue
     long_ret=lp1/lp0-1.0
     short_ret=1.0-sp1/sp0
     basis_pnl_bps=(long_ret+short_ret)*10000
     fund=(-sum_funding(L["fund"],start,end)+sum_funding(S["fund"],start,end))*10000
     net=basis_pnl_bps+fund-rt
     results.append({
      "base":route["base"],"long_provider":lname,"short_provider":sname,
      "hold_hours":h,"entry_ts":start,"exit_ts":end,
      "long_entry":lp0,"long_exit":lp1,"short_entry":sp0,"short_exit":sp1,
      "basis_pnl_bps":basis_pnl_bps,"funding_capture_bps":fund,
      "fees_plus_execution_bps":rt,"net_bps":net
     })
  except Exception as e:
   errors.append({"route":route,"error":f"{type(e).__name__}: {e}"[:800]})

 with (OUT/"crypto_pair_backtest_windows.csv").open("w",newline="",encoding="utf-8") as f:
  if results:
   w=csv.DictWriter(f,fieldnames=list(results[0].keys()));w.writeheader();w.writerows(results)

 groups={}
 for r in results:
  k=(r["base"],r["long_provider"],r["short_provider"],r["hold_hours"])
  groups.setdefault(k,[]).append(r["net_bps"])
 summary=[]
 for k,xs in groups.items():
  xs2=sorted(xs);n=len(xs)
  summary.append({
   "base":k[0],"long_provider":k[1],"short_provider":k[2],"hold_hours":k[3],
   "n":n,"positive_pct":100*sum(x>0 for x in xs)/n,
   "median_net_bps":statistics.median(xs),
   "mean_net_bps":statistics.fmean(xs),
   "p05_net_bps":xs2[max(0,round((n-1)*.05))],
   "p95_net_bps":xs2[min(n-1,round((n-1)*.95))],
   "max_net_bps":max(xs),"min_net_bps":min(xs)
  })
 summary.sort(key=lambda r:(r["median_net_bps"],r["positive_pct"]),reverse=True)
 (OUT/"crypto_pair_backtest_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
 (OUT/"crypto_pair_backtest_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(summary[:30],ensure_ascii=False,indent=2))

if __name__=="__main__":main()
