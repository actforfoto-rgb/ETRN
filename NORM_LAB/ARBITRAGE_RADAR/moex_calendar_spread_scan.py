from __future__ import annotations

import csv, json, math
from datetime import date
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"
R=0.14
EXEC_BUFFER_POINTS=2.0

ASSET_TO_SPOT={
"SBRF":"SBER","SBPR":"SBERP","GAZR":"GAZP","GMKN":"GMKN","LKOH":"LKOH","ROSN":"ROSN",
"YDEX":"YDEX","VTBR":"VTBR","NOTK":"NVTK","PLZL":"PLZL","FEES":"FEES","HEAD":"HEAD",
"SIBN":"SIBN","SOFL":"SOFL","AFLT":"AFLT","AFKS":"AFKS","TATN":"TATN","TATP":"TATNP",
"RTKM":"RTKM","RTKMP":"RTKMP","MGNT":"MGNT","MTSS":"MTSS","MOEX":"MOEX","NLMK":"NLMK",
"CHMF":"CHMF","ALRS":"ALRS","PHOR":"PHOR","RUAL":"RUAL","IRAO":"IRAO","MAGN":"MAGN",
"FLOT":"FLOT","HYDR":"HYDR","TRNF":"TRNFP"
}

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def main():
 j=get(f"{BASE}/engines/futures/markets/forts/securities.json",{"iss.meta":"off","iss.only":"securities,marketdata"})
 sec=rows(j,"securities");md={r.get("SECID"):r for r in rows(j,"marketdata")}
 by={}
 for s in sec:
  asset=s.get("ASSETCODE")
  if asset not in ASSET_TO_SPOT:continue
  sid=s.get("SECID");exp=s.get("LASTTRADEDATE")
  if not sid or not exp or sid in ("SBERF","GAZPF"):continue
  try: ed=date.fromisoformat(exp)
  except:continue
  if ed<=date(2026,9,29):continue
  m=md.get(sid,{})
  bid=num(m.get("BID"));ask=num(m.get("OFFER"))
  if bid is None or ask is None:continue
  by.setdefault(asset,[]).append({
    "secid":sid,"expiry":ed,"lot":num(s.get("LOTVOLUME")) or 1,
    "bid":bid,"ask":ask,"fee":num(s.get("BUYSELLFEE")) or 0,
    "value":num(m.get("VALTODAY")) or 0,"oi":num(m.get("OPENPOSITION")) or 0
  })

 out=[]
 for asset,arr in by.items():
  arr.sort(key=lambda x:x["expiry"])
  for near,far in zip(arr,arr[1:]):
   if near["lot"]!=far["lot"]:continue
   spread_id=near["secid"]+far["secid"]
   try:
    sj=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{spread_id}.json",{"iss.meta":"off"})
    sm=rows(sj,"marketdata")
    if not sm:continue
    m=sm[0]
    sb=num(m.get("BID"));sa=num(m.get("OFFER"));sl=num(m.get("LAST"))
   except:continue
   if sb is None or sa is None:continue

   near_mid=(near["bid"]+near["ask"])/2
   dt=(far["expiry"]-near["expiry"]).days/365
   fair_far=near_mid*math.exp(R*dt)
   fair_spread=fair_far-near_mid
   mid=(sb+sa)/2
   # Buy spread at ask when under fair; sell spread at bid when over fair.
   under_edge=fair_spread-sa
   over_edge=sb-fair_spread
   best=max(under_edge,over_edge)
   direction="BUY_SPREAD" if under_edge>=over_edge else "SELL_SPREAD"
   exec_edge=best-EXEC_BUFFER_POINTS
   denom=max(1.0,near_mid)
   ann=(exec_edge/denom)/dt*100 if dt>0 else None
   out.append({
    "asset":asset,"spot":ASSET_TO_SPOT[asset],"near":near["secid"],"far":far["secid"],
    "spread_id":spread_id,"near_expiry":near["expiry"].isoformat(),"far_expiry":far["expiry"].isoformat(),
    "near_mid_points":near_mid,"spread_bid":sb,"spread_ask":sa,"spread_last":sl,
    "fair_spread_points_key14_no_div":fair_spread,"direction":direction,
    "gross_edge_points":best,"exec_buffer_points":EXEC_BUFFER_POINTS,
    "screen_net_edge_points":exec_edge,"screen_annualized_edge_pct":ann,
    "near_value":near["value"],"near_oi":near["oi"],"far_value":far["value"],"far_oi":far["oi"],
    "dividend_between_expiries_unmodeled":"YES"
   })

 out.sort(key=lambda x:x["screen_net_edge_points"],reverse=True)
 if out:
  with (OUT/"moex_calendar_spread_screen.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(out[0].keys()));w.writeheader();w.writerows(out)
 (OUT/"moex_calendar_spread_top.json").write_text(json.dumps(out[:50],ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(out[:20],ensure_ascii=False,indent=2))

if __name__=="__main__":main()
