from __future__ import annotations

import csv, json, math, statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

# Completed 2026 cycles + current December cycle for live diagnostics.
FAMILIES=[
 {"name":"CNY","perp":"CNYRUBF","short_prefix":"CNY","months":[3,6,9,12]},
 {"name":"USD","perp":"USDRUBF","short_prefix":"Si","months":[3,6,9,12]},
 {"name":"EUR","perp":"EURRUBF","short_prefix":"Eu","months":[3,6,9,12]},
 {"name":"IMOEX","perp":"IMOEXF","short_prefix":"MXI","months":[3,6,9,12]},
]
THRESHOLDS=[1.0,1.10,1.25,1.50,2.0,3.0]
MIN_DAYS=5
MAX_DAYS=90
KEY_REGIMES=[
 ("2026-01-01",16.00),
 ("2026-02-16",15.50),
 ("2026-03-23",15.00),
 ("2026-04-27",14.50),
 ("2026-06-22",14.25),
 ("2026-07-27",14.00),
]

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def search_short(shortname):
 j=get(f"{BASE}/securities.json",{"q":shortname,"iss.meta":"off"})
 hits=rows(j,"securities")
 def val(r,k):return r.get(k) if r.get(k) is not None else r.get(k.upper())
 exact=[r for r in hits if str(val(r,"shortname") or "").upper()==shortname.upper()]
 fut=[r for r in exact if "future" in str(val(r,"type") or "").lower()]
 hit=(fut or exact or hits[:1])
 if not hit:return None
 r=hit[0]
 return val(r,"secid")

def current_meta(secid):
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
 s=rows(j,"securities");m=rows(j,"marketdata")
 if not s:return None
 a=s[0];b=m[0] if m else {}
 ms=num(a.get("MINSTEP")) or 1.0
 sp=num(a.get("STEPPRICE")) or ms
 return {
  "unit":sp/ms,"im":num(a.get("INITIALMARGIN")) or 0.0,
  "fee":num(a.get("BUYSELLFEE")) or 0.0,
  "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
  "expiry":a.get("LASTTRADEDATE")
 }

def hist(secid,frm="2026-01-01",till="2026-12-31"):
 url=f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json"
 out=[];start=0
 while True:
  j=get(url,{"from":frm,"till":till,"start":start,"iss.meta":"off"})
  rr=rows(j,"history");out.extend(rr)
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

def swap(r):
 return num(r.get("SWAPRATE") or r.get("SWAPRATE_CURR"))

def rate_on(d):
 r=16.0
 for start,val in KEY_REGIMES:
  if d>=start:r=val
 return r/100

def capital_cost(im,start,end):
 a=date.fromisoformat(start);b=date.fromisoformat(end)
 cur=a;cost=0.0
 while cur<b:
  cost += im*rate_on(cur.isoformat())/365
  cur += timedelta(days=1)
 return cost

def business_days(a,b):
 n=0;cur=a
 while cur<b:
  cur+=timedelta(days=1)
  if cur.weekday()<5:n+=1
 return n

def infer_scale(q,p):
 cands=[0.001,0.01,0.1,1,10,100,1000,10000]
 return min(cands,key=lambda s:abs(math.log(max(1e-12,(q/s)/p))))

def contract_short(prefix,m):
 return f"{prefix}-{m}.26"

def cycle(fam,quarter_sid,quarter_short,perp_meta,quarter_meta):
 qh=hist(quarter_sid)
 ph=hist(fam["perp"])
 q={r.get("TRADEDATE"):r for r in qh}
 p={r.get("TRADEDATE"):r for r in ph}
 common=sorted(set(q)&set(p))
 if len(common)<3:return []

 # Actual last overlapping day approximates expiry close.
 exit_date=common[-1]
 q_exit=settle(q[exit_date]);p_exit=settle(p[exit_date])
 if q_exit is None or p_exit is None:return []

 # Scale contract sensitivities from overlapping prices.
 first=common[0]
 q0=settle(q[first]);p0=settle(p[first])
 scale=infer_scale(q0,p0)
 qsens=quarter_meta["unit"]*scale
 psens=perp_meta["unit"]
 qqty=psens/qsens

 # Conservative current fee/IM/spread proxies.
 combined_im=qqty*quarter_meta["im"]+perp_meta["im"]
 fees=2*(qqty*quarter_meta["fee"]+perp_meta["fee"])
 current_spread=0.0
 if quarter_meta["bid"] is not None and quarter_meta["ask"] is not None:
  current_spread+=(quarter_meta["ask"]-quarter_meta["bid"])*qqty*quarter_meta["unit"]
 if perp_meta["bid"] is not None and perp_meta["ask"] is not None:
  current_spread+=(perp_meta["ask"]-perp_meta["bid"])*perp_meta["unit"]
 execution_buffer=2*current_spread

 out=[]
 for d in common[:-1]:
  q_entry=settle(q[d]);p_entry=settle(p[d]);sw=swap(p[d])
  if q_entry is None or p_entry is None or sw is None:continue
  days=(date.fromisoformat(exit_date)-date.fromisoformat(d)).days
  work=business_days(date.fromisoformat(d),date.fromisoformat(exit_date))
  if days<MIN_DAYS or days>MAX_DAYS or work<=0:continue

  if sw>=0:
   direction="LONG_QUARTERLY_SHORT_PERP"
   # Cost of entering the fixed-vs-perp gap if held to expiry.
   entry_gap=q_entry*qqty*quarter_meta["unit"]-p_entry*perp_meta["unit"]
   q_pnl=(q_exit-q_entry)*qqty*quarter_meta["unit"]
   p_pnl=(p_entry-p_exit)*perp_meta["unit"]
   funding=sum((swap(p[x]) or 0)*perp_meta["unit"]
               for x in common if d<x<=exit_date)
  else:
   direction="SHORT_QUARTERLY_LONG_PERP"
   entry_gap=p_entry*perp_meta["unit"]-q_entry*qqty*quarter_meta["unit"]
   q_pnl=(q_entry-q_exit)*qqty*quarter_meta["unit"]
   p_pnl=(p_exit-p_entry)*perp_meta["unit"]
   funding=-sum((swap(p[x]) or 0)*perp_meta["unit"]
                for x in common if d<x<=exit_date)

  carry=capital_cost(combined_im,d,exit_date)
  required=(entry_gap+carry+fees+execution_buffer)/(perp_meta["unit"]*work)
  ratio=(abs(sw)/required) if required>0 else 99.0
  net=q_pnl+p_pnl+funding-carry-fees-execution_buffer
  out.append({
   "family":fam["name"],"quarter":quarter_sid,"quarter_short":quarter_short,
   "entry":d,"exit":exit_date,"days":days,"business_days":work,
   "direction":direction,"entry_swaprate":sw,"required_swaprate":required,
   "swaprate_ratio":ratio,"entry_gap_rub":entry_gap,
   "quarter_pnl_rub":q_pnl,"perp_pnl_rub":p_pnl,"funding_rub":funding,
   "capital_cost_rub":carry,"fees_rub":fees,"execution_buffer_rub":execution_buffer,
   "net_rub":net,"return_on_current_im_pct":net/combined_im*100 if combined_im else None,
   "combined_im_rub":combined_im
  })
 return out

def main():
 allrows=[];resolution=[]
 for fam in FAMILIES:
  pmeta=current_meta(fam["perp"])
  if not pmeta:
   resolution.append({"family":fam["name"],"error":"perp metadata missing"})
   continue
  for m in fam["months"]:
   short=contract_short(fam["short_prefix"],m)
   sid=search_short(short)
   if not sid:
    resolution.append({"family":fam["name"],"short":short,"error":"quarter unresolved"})
    continue
   qmeta=current_meta(sid)
   # Expired contracts may not expose current marketdata/IM. Fall back to current
   # December contract metadata of same family for unit/fees/IM proxy.
   if not qmeta or qmeta["im"]<=0:
    current_sid=search_short(contract_short(fam["short_prefix"],12))
    qmeta=current_meta(current_sid) if current_sid else None
   if not qmeta:
    resolution.append({"family":fam["name"],"short":short,"sid":sid,"error":"metadata missing"})
    continue
   rr=cycle(fam,sid,short,pmeta,qmeta)
   allrows.extend(rr)
   resolution.append({"family":fam["name"],"short":short,"sid":sid,"rows":len(rr)})

 (OUT/"moex_expiry_cycle_resolution.json").write_text(json.dumps(resolution,ensure_ascii=False,indent=2),encoding="utf-8")
 if allrows:
  with (OUT/"moex_expiry_cycle_entries.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(allrows[0].keys()));w.writeheader();w.writerows(allrows)

 summary=[]
 for fam in FAMILIES:
  famrows=[r for r in allrows if r["family"]==fam["name"]]
  for th in THRESHOLDS:
   xs=[r for r in famrows if r["swaprate_ratio"]>=th]
   if not xs:continue
   nets=[r["return_on_current_im_pct"] for r in xs]
   summary.append({
    "family":fam["name"],"ratio_threshold":th,"n_entries":len(xs),
    "positive_pct":100*sum(x>0 for x in nets)/len(nets),
    "median_return_on_im_pct":statistics.median(nets),
    "mean_return_on_im_pct":statistics.fmean(nets),
    "aggregate_net_rub":sum(r["net_rub"] for r in xs),
    "worst_return_on_im_pct":min(nets),"best_return_on_im_pct":max(nets),
    "cycles":sorted(set(r["quarter_short"] for r in xs))
   })
 summary.sort(key=lambda x:(x["family"],x["ratio_threshold"]))
 (OUT/"moex_expiry_cycle_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
