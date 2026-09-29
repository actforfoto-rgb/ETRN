from __future__ import annotations

import csv, json, statistics
from datetime import date
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"
FROM="2026-01-01"; TILL="2026-09-29"; KEY=0.14

PAIRS=[
 {"name":"CNY","quarter":"CRZ6","perp":"CNYRUBF"},
 {"name":"USD","quarter":"SiZ6","perp":"USDRUBF"},
 {"name":"EUR","quarter":"EuZ6","perp":"EURRUBF"},
 {"name":"IMOEX","quarter":"MMZ6","perp":"IMOEXF"},
 {"name":"RGBI","quarter":"RBZ6","perp":"RGBIF"},
]

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25); r.raise_for_status(); return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def current(secid):
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
 s=rows(j,"securities"); m=rows(j,"marketdata")
 if not s:return None
 a=s[0]; b=m[0] if m else {}
 ms=num(a.get("MINSTEP")) or 1.0
 sp=num(a.get("STEPPRICE")) or ms
 return {
  "sid":secid,"lot":num(a.get("LOTVOLUME")) or 1.0,
  "unit":sp/ms,"im":num(a.get("INITIALMARGIN")) or 0.0,
  "fee":num(a.get("BUYSELLFEE")) or 0.0,
  "expiry":a.get("LASTTRADEDATE"),
  "bid":num(b.get("BID")),"ask":num(b.get("OFFER"))
 }

def hist(secid):
 url=f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json"
 out=[]; start=0
 while True:
  j=get(url,{"from":FROM,"till":TILL,"start":start,"iss.meta":"off"})
  rr=rows(j,"history"); out.extend(rr)
  cur=rows(j,"history.cursor")
  total=int(cur[0].get("TOTAL") or 0) if cur else len(out)
  if not rr or len(out)>=total:break
  start=len(out)
 return out

def settle(r):
 for k in ("SETTLEPRICE","CLOSE","WAPRICE"):
  v=num(r.get(k))
  if v is not None:return v
 return None

def infer_scale(q0,p0):
 cands=[0.0001,0.001,0.01,0.1,1,10,100,1000,10000]
 import math
 return min(cands,key=lambda s:abs(math.log(max(1e-12,(q0/s)/p0))))

def backtest(cfg):
 qcur=current(cfg["quarter"]); pcur=current(cfg["perp"])
 if not qcur or not pcur:return {"name":cfg["name"],"error":"current metadata missing"}
 qh={r.get("TRADEDATE"):r for r in hist(cfg["quarter"])}
 ph={r.get("TRADEDATE"):r for r in hist(cfg["perp"])}
 dates=sorted(set(qh)&set(ph))
 raw=[]
 for d in dates:
  qp=settle(qh[d]); pp=settle(ph[d]); sw=num(ph[d].get("SWAPRATE") or ph[d].get("SWAPRATE_CURR"))
  if qp is None or pp is None or sw is None:continue
  raw.append({"date":d,"q":qp,"p":pp,"swap":sw})
 if len(raw)<5:return {"name":cfg["name"],"error":"insufficient overlap","overlap":len(raw)}

 scale=infer_scale(raw[0]["q"],raw[0]["p"])
 qsens=qcur["unit"]*scale
 psens=pcur["unit"]
 qqty=psens/qsens
 # Use current IM/fees as conservative capital proxy for historical screen.
 cap=qqty*qcur["im"]+pcur["im"]
 fees=2*(qqty*qcur["fee"]+pcur["fee"])
 # today’s full spread as a conservative execution buffer where available
 spreadbuf=0.0
 if qcur["bid"] is not None and qcur["ask"] is not None:
  spreadbuf += (qcur["ask"]-qcur["bid"])*qqty*qcur["unit"]
 if pcur["bid"] is not None and pcur["ask"] is not None:
  spreadbuf += (pcur["ask"]-pcur["bid"])*pcur["unit"]

 windows=[]
 for n in (3,5,10,20,40):
  for i in range(len(raw)-n):
   a=raw[i]; b=raw[i+n]
   # Dynamic direction chosen using entry swap sign.
   if a["swap"]>=0:
    direction="LONG_QUARTERLY_SHORT_PERP"
    q_pnl=(b["q"]-a["q"])*qqty*qcur["unit"]
    p_pnl=(a["p"]-b["p"])*pcur["unit"]
    funding=sum(x["swap"] for x in raw[i+1:i+n+1])*pcur["unit"]
   else:
    direction="SHORT_QUARTERLY_LONG_PERP"
    q_pnl=(a["q"]-b["q"])*qqty*qcur["unit"]
    p_pnl=(b["p"]-a["p"])*pcur["unit"]
    funding=-sum(x["swap"] for x in raw[i+1:i+n+1])*pcur["unit"]
   cal=(date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days
   margin_cost=cap*KEY*cal/365
   net=q_pnl+p_pnl+funding-margin_cost-fees-spreadbuf
   windows.append({
    "name":cfg["name"],"direction":direction,"n_obs":n,
    "start":a["date"],"end":b["date"],"calendar_days":cal,
    "entry_swaprate":a["swap"],"quarter_pnl_rub":q_pnl,"perp_pnl_rub":p_pnl,
    "funding_rub":funding,"margin_cost_rub":margin_cost,
    "roundtrip_fee_rub":fees,"spread_buffer_rub":spreadbuf,
    "screen_net_rub":net,"screen_return_on_current_im_pct":net/cap*100 if cap else None
   })
 windows.sort(key=lambda x:x["screen_return_on_current_im_pct"],reverse=True)
 positives=[x for x in windows if x["screen_net_rub"]>0]
 summary={
  "name":cfg["name"],"quarter":cfg["quarter"],"perp":cfg["perp"],
  "overlap_days":len(raw),"scale":scale,"quarter_qty_per_perp":qqty,
  "combined_current_im_rub":cap,"roundtrip_fee_rub":fees,"spread_buffer_rub":spreadbuf,
  "windows":len(windows),"positive_windows":len(positives),
  "positive_windows_pct":100*len(positives)/len(windows) if windows else 0,
  "median_return_on_im_pct":statistics.median(x["screen_return_on_current_im_pct"] for x in windows) if windows else None,
  "best":windows[:20]
 }
 with (OUT/f"moex_curve_{cfg['name'].lower()}_windows.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=list(windows[0].keys()));w.writeheader();w.writerows(windows)
 return summary

def main():
 out=[]
 for cfg in PAIRS:
  try: out.append(backtest(cfg))
  except Exception as e: out.append({"name":cfg["name"],"error":f"{type(e).__name__}: {e}"})
 (OUT/"moex_curve_backtest_summary.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
