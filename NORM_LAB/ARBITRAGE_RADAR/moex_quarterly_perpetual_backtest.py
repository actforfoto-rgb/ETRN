from __future__ import annotations

import csv, json, statistics
from datetime import date
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

PAIRS=[
 {"name":"SBER_QP","quarter":"SRZ6","perp":"SBERF","lot":100,"q_im":5224.48,"p_im":4649.76,
  "q_fee":5.58,"p_fee":8.19,"exclude":[("2026-07-17","2026-07-21")]},
 {"name":"GAZP_QP","quarter":"GZZ6","perp":"GAZPF","lot":100,"q_im":1878.52,"p_im":1731.66,
  "q_fee":2.00,"p_fee":3.04,"exclude":[]},
]
FROM="2026-01-01";TILL="2026-09-29";KEY=0.14;EXEC_BUFFER_RUB=10.0

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,r)) for r in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def hist(secid):
 url=f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json"
 out=[];start=0
 while True:
  j=get(url,{"from":FROM,"till":TILL,"start":start,"iss.meta":"off"})
  rr=rows(j,"history");out.extend(rr)
  cur=rows(j,"history.cursor"); total=int(cur[0].get("TOTAL") or 0) if cur else len(out)
  if not rr or len(out)>=total:break
  start=len(out)
 return out

def settle(r):
 for k in ("SETTLEPRICE","CLOSE","WAPRICE"):
  v=num(r.get(k))
  if v is not None:return v
 return None

def excluded(p,a,b):
 for x,y in p["exclude"]:
  if a<=y and b>=x:return True
 return False

def run_pair(p):
 q={r.get("TRADEDATE"):r for r in hist(p["quarter"])}
 z={r.get("TRADEDATE"):r for r in hist(p["perp"])}
 dates=sorted(set(q)&set(z))
 daily=[]
 for d in dates:
  qp=settle(q[d]);pp=settle(z[d]);sw=num(z[d].get("SWAPRATE") or z[d].get("SWAPRATE_CURR"))
  if qp is None or pp is None or sw is None:continue
  daily.append({"date":d,"q":qp,"p":pp,"swap":sw})
 if not daily:return {"pair":p["name"],"error":"no overlap"}

 results=[]
 for n in (3,5,10,20,40):
  for i in range(len(daily)-n):
   a=daily[i];b=daily[i+n]
   if excluded(p,a["date"],b["date"]):continue
   # Long quarterly, short perpetual
   q_pnl=b["q"]-a["q"]
   p_pnl=(a["p"]-b["p"])*p["lot"]
   funding=sum(x["swap"] for x in daily[i+1:i+n+1])*p["lot"]
   gross=q_pnl+p_pnl+funding
   cal=(date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days
   margin_cost=(p["q_im"]+p["p_im"])*KEY*cal/365
   rt=2*(p["q_fee"]+p["p_fee"])+EXEC_BUFFER_RUB
   net=gross-margin_cost-rt
   cap=p["q_im"]+p["p_im"]
   results.append({
    "pair":p["name"],"n_obs":n,"start":a["date"],"end":b["date"],"calendar_days":cal,
    "quarter_entry":a["q"],"quarter_exit":b["q"],
    "perp_entry":a["p"],"perp_exit":b["p"],
    "quarter_pnl_rub":q_pnl,"perp_pnl_rub":p_pnl,"funding_rub":funding,
    "gross_rub":gross,"margin_cost_rub":margin_cost,"roundtrip_buffer_rub":rt,
    "screen_net_rub":net,"screen_return_on_current_im_pct":net/cap*100
   })
 results.sort(key=lambda x:x["screen_return_on_current_im_pct"],reverse=True)
 if results:
  with (OUT/f"moex_{p['name'].lower()}_windows.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(results[0].keys()));w.writeheader();w.writerows(results)
 pos=[x for x in results if x["screen_net_rub"]>0]
 return {
  "pair":p["name"],"overlap_days":len(daily),"windows":len(results),
  "positive_windows":len(pos),"positive_windows_pct":100*len(pos)/len(results) if results else 0,
  "best":results[:25],
  "median_return_on_im_pct":statistics.median(x["screen_return_on_current_im_pct"] for x in results) if results else None
 }

def main():
 out=[]
 for p in PAIRS:
  try:out.append(run_pair(p))
  except Exception as e:out.append({"pair":p["name"],"error":f"{type(e).__name__}: {e}"})
 (OUT/"moex_quarterly_perpetual_backtest_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
