from __future__ import annotations

import csv, json, math, statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()
KEY_RATE=0.14

FAMILIES=[
 {"name":"BTC","perp":"BTCUSDF","fixed_current":"BTV6","fixed_sep":"BTU6"},
 {"name":"ETH","perp":"ETHUSDF","fixed_current":"EHV6","fixed_sep":"EHU6"},
 {"name":"SOL","perp":"SOLUSDF","fixed_current":"S3V6","fixed_sep":"S3U6"},
 {"name":"XRP","perp":"XRPUSDF","fixed_current":"XRV6","fixed_sep":"XRU6"},
 {"name":"TRX","perp":"TRXUSDF","fixed_current":"TXV6","fixed_sep":"TXU6"},
]
HOLD_DAYS=[1,2,3,4]
ENTRY_RATIO_THRESHOLDS=[1.0,1.25,1.5,2.0]

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def meta(sid):
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{sid}.json",{"iss.meta":"off"})
 ss=rows(j,"securities");mm=rows(j,"marketdata")
 if not ss:return None
 a=ss[0];b=mm[0] if mm else {}
 ms=num(a.get("MINSTEP")) or 1.0
 sp=num(a.get("STEPPRICE")) or ms
 return {
  "sid":sid,"unit":sp/ms,"lot":num(a.get("LOTVOLUME")) or 1.0,
  "im":num(a.get("INITIALMARGIN")) or 0.0,
  "fee":num(a.get("BUYSELLFEE")) or 0.0,
  "exercise_fee":num(a.get("EXERCISEFEE")) or 0.0,
  "expiry":a.get("LASTTRADEDATE"),
  "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
  "settle":num(b.get("SETTLEPRICE")),"swap":num(b.get("SWAPRATE")),
  "swap_curr":num(b.get("SWAPRATE_CURR")),
  "trades":num(b.get("NUMTRADES")) or 0,
  "volume":num(b.get("VOLTODAY")) or 0,
  "oi":num(b.get("OPENPOSITION")) or 0,
 }

def hist(sid,frm="2026-09-01",till=None):
 if till is None:till=TODAY.isoformat()
 url=f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/securities/{sid}.json"
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
 return num(r.get("SWAPRATE"))

def bdays(a,b):
 n=0;cur=a
 while cur<b:
  cur+=timedelta(days=1)
  if cur.weekday()<5:n+=1
 return n

def carry(im,days):
 return im*KEY_RATE*days/365

def current_screen(fam):
 p=meta(fam["perp"]);q=meta(fam["fixed_current"])
 if not p or not q:return {"family":fam["name"],"error":"metadata missing"}
 if None in (p["bid"],p["ask"],p["swap"],q["bid"],q["ask"]):
  return {"family":fam["name"],"error":"bid/ask/swap missing"}

 # Hedge by RUB P&L sensitivity to one quoted price unit.
 q_qty=p["unit"]/q["unit"]
 exp=date.fromisoformat(q["expiry"])
 days=max(0,(exp-TODAY).days)
 work=bdays(TODAY,exp)
 margin=p["im"]+q_qty*q["im"]
 fees=2*(p["fee"]+q_qty*q["fee"])+q_qty*q["exercise_fee"]
 spread_buffer=(p["ask"]-p["bid"])*p["unit"]+(q["ask"]-q["bid"])*q["unit"]*q_qty

 if p["swap"]>=0:
  direction="LONG_FIXED_SHORT_PERP"
  convergence=p["bid"]*p["unit"]-q["ask"]*q["unit"]*q_qty
  funding_per_day=p["swap"]*p["lot"]
 else:
  direction="SHORT_FIXED_LONG_PERP"
  convergence=q["bid"]*q["unit"]*q_qty-p["ask"]*p["unit"]
  funding_per_day=-p["swap"]*p["lot"]

 projected=funding_per_day*work
 capital=carry(margin,days)
 net=convergence+projected-capital-fees-spread_buffer
 required=(capital+fees+spread_buffer-convergence)/(p["lot"]*work) if work and p["lot"] else None
 ratio=(funding_per_day/required) if required and required>0 else None

 return {
  "family":fam["name"],"perp":fam["perp"],"fixed":fam["fixed_current"],
  "direction":direction,"expiry":q["expiry"],"calendar_days":days,"business_days":work,
  "perp_bid":p["bid"],"perp_ask":p["ask"],"fixed_bid":q["bid"],"fixed_ask":q["ask"],
  "perp_unit_rub_per_price":p["unit"],"fixed_unit_rub_per_price":q["unit"],
  "fixed_qty_per_perp":q_qty,"perp_lot_for_funding":p["lot"],
  "swaprate_rub_per_lot_unit":p["swap"],"funding_rub_per_day":funding_per_day,
  "convergence_pnl_if_same_price_rub":convergence,
  "projected_funding_if_current_rate_rub":projected,
  "combined_im_rub":margin,"capital_cost_key14_rub":capital,
  "roundtrip_fees_plus_exercise_rub":fees,"full_spread_buffer_rub":spread_buffer,
  "required_abs_swaprate_rub":required,"swaprate_to_breakeven_ratio":ratio,
  "conditional_net_rub":net,"conditional_return_on_im_pct":net/margin*100 if margin else None,
  "perp_trades":p["trades"],"fixed_trades":q["trades"],"perp_oi":p["oi"],"fixed_oi":q["oi"],
  "index_div_status":"UNVERIFIED_PUBLIC_HISTORY_FIELD",
  "status":"RESEARCH_ONLY"
 }

def rolling_backtest(fam):
 pmeta=meta(fam["perp"]);qmeta=meta(fam["fixed_current"])
 if not pmeta or not qmeta:return []
 ph={r.get("TRADEDATE"):r for r in hist(fam["perp"]) if r.get("TRADEDATE")}
 qh={r.get("TRADEDATE"):r for r in hist(fam["fixed_current"]) if r.get("TRADEDATE")}
 dates=sorted(set(ph)&set(qh))
 if len(dates)<2:return []
 q_qty=pmeta["unit"]/qmeta["unit"]
 margin=pmeta["im"]+q_qty*qmeta["im"]
 fees=2*(pmeta["fee"]+q_qty*qmeta["fee"])
 spread_buffer=0.0
 if pmeta["bid"] is not None and pmeta["ask"] is not None:
  spread_buffer+=(pmeta["ask"]-pmeta["bid"])*pmeta["unit"]
 if qmeta["bid"] is not None and qmeta["ask"] is not None:
  spread_buffer+=(qmeta["ask"]-qmeta["bid"])*qmeta["unit"]*q_qty

 out=[]
 for i,d in enumerate(dates[:-1]):
  p0=settle(ph[d]);q0=settle(qh[d]);sw=swap(ph[d])
  if None in (p0,q0,sw):continue
  direction="LONG_FIXED_SHORT_PERP" if sw>=0 else "SHORT_FIXED_LONG_PERP"
  for hold in HOLD_DAYS:
   j=i+hold
   if j>=len(dates):continue
   e=dates[j];p1=settle(ph[e]);q1=settle(qh[e])
   if p1 is None or q1 is None:continue
   if direction=="LONG_FIXED_SHORT_PERP":
    q_pnl=(q1-q0)*qmeta["unit"]*q_qty
    p_pnl=(p0-p1)*pmeta["unit"]
    funding=sum((swap(ph[x]) or 0.0)*pmeta["lot"] for x in dates[i+1:j+1])
   else:
    q_pnl=(q0-q1)*qmeta["unit"]*q_qty
    p_pnl=(p1-p0)*pmeta["unit"]
    funding=-sum((swap(ph[x]) or 0.0)*pmeta["lot"] for x in dates[i+1:j+1])
   cal=(date.fromisoformat(e)-date.fromisoformat(d)).days
   net=q_pnl+p_pnl+funding-carry(margin,cal)-fees-spread_buffer
   out.append({
    "family":fam["name"],"entry":d,"exit":e,"hold_trading_days":hold,"calendar_days":cal,
    "direction":direction,"entry_swaprate":sw,"fixed_pnl_rub":q_pnl,"perp_pnl_rub":p_pnl,
    "funding_rub":funding,"capital_cost_rub":carry(margin,cal),"fees_rub":fees,
    "spread_buffer_rub":spread_buffer,"net_rub":net,
    "return_on_im_pct":net/margin*100 if margin else None,
    "index_div_status":"NOT_INCLUDED_UNVERIFIED"
   })
 return out

def september_expiry_test(fam):
 pmeta=meta(fam["perp"]);proxy=meta(fam["fixed_current"])
 if not pmeta or not proxy:return []
 ph={r.get("TRADEDATE"):r for r in hist(fam["perp"]) if r.get("TRADEDATE")}
 qrows=hist(fam["fixed_sep"])
 qh={r.get("TRADEDATE"):r for r in qrows if r.get("TRADEDATE")}
 dates=sorted(set(ph)&set(qh))
 if len(dates)<2:return []
 exit_date=dates[-1]
 qexit=settle(qh[exit_date]);pexit=settle(ph[exit_date])
 if qexit is None or pexit is None:return []
 q_qty=pmeta["unit"]/proxy["unit"]
 margin=pmeta["im"]+q_qty*proxy["im"]
 fees=2*(pmeta["fee"]+q_qty*proxy["fee"])+q_qty*proxy["exercise_fee"]
 out=[]
 for d in dates[:-1]:
  p0=settle(ph[d]);q0=settle(qh[d]);sw=swap(ph[d])
  if None in (p0,q0,sw):continue
  work=bdays(date.fromisoformat(d),date.fromisoformat(exit_date))
  cal=(date.fromisoformat(exit_date)-date.fromisoformat(d)).days
  if work<=0:continue
  if sw>=0:
   direction="LONG_FIXED_SHORT_PERP"
   convergence=p0*pmeta["unit"]-q0*proxy["unit"]*q_qty
   fixed_pnl=(qexit-q0)*proxy["unit"]*q_qty
   perp_pnl=(p0-pexit)*pmeta["unit"]
   actual_funding=sum((swap(ph[x]) or 0.0)*pmeta["lot"] for x in dates if d<x<=exit_date)
   current_funding=sw*pmeta["lot"]
  else:
   direction="SHORT_FIXED_LONG_PERP"
   convergence=q0*proxy["unit"]*q_qty-p0*pmeta["unit"]
   fixed_pnl=(q0-qexit)*proxy["unit"]*q_qty
   perp_pnl=(pexit-p0)*pmeta["unit"]
   actual_funding=-sum((swap(ph[x]) or 0.0)*pmeta["lot"] for x in dates if d<x<=exit_date)
   current_funding=-sw*pmeta["lot"]
  capital=carry(margin,cal)
  # No historical bid/ask archive in this quick test: use 2x current full spread proxy.
  spread=0.0
  if pmeta["bid"] is not None and pmeta["ask"] is not None:
   spread+=(pmeta["ask"]-pmeta["bid"])*pmeta["unit"]
  if proxy["bid"] is not None and proxy["ask"] is not None:
   spread+=(proxy["ask"]-proxy["bid"])*proxy["unit"]*q_qty
  execbuf=2*spread
  required=(capital+fees+execbuf-convergence)/(pmeta["lot"]*work) if pmeta["lot"] else None
  ratio=current_funding/required if required and required>0 else None
  net=fixed_pnl+perp_pnl+actual_funding-capital-fees-execbuf
  out.append({
   "family":fam["name"],"entry":d,"exit":exit_date,"direction":direction,
   "entry_swaprate":sw,"required_swaprate":required,"swaprate_ratio":ratio,
   "fixed_pnl_rub":fixed_pnl,"perp_pnl_rub":perp_pnl,"funding_rub":actual_funding,
   "capital_cost_rub":capital,"fees_rub":fees,"execution_buffer_rub":execbuf,
   "net_rub":net,"return_on_im_pct":net/margin*100 if margin else None,
   "index_div_status":"NOT_INCLUDED_UNVERIFIED"
  })
 return out

def main():
 current=[];rolling=[];expiry=[]
 for fam in FAMILIES:
  try:current.append(current_screen(fam))
  except Exception as e:current.append({"family":fam["name"],"error":f"{type(e).__name__}: {e}"})
  try:rolling.extend(rolling_backtest(fam))
  except Exception:pass
  try:expiry.extend(september_expiry_test(fam))
  except Exception:pass

 current.sort(key=lambda x:x.get("conditional_return_on_im_pct") if x.get("conditional_return_on_im_pct") is not None else -999,reverse=True)
 (OUT/"moex_crypto_curve_current.json").write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding="utf-8")

 if rolling:
  with (OUT/"moex_crypto_curve_rolling.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(rolling[0].keys()));w.writeheader();w.writerows(rolling)
 if expiry:
  with (OUT/"moex_crypto_curve_sep_expiry.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(expiry[0].keys()));w.writeheader();w.writerows(expiry)

 summary=[]
 for fam in FAMILIES:
  for hold in HOLD_DAYS:
   xs=[r["return_on_im_pct"] for r in rolling if r["family"]==fam["name"] and r["hold_trading_days"]==hold]
   if xs:
    summary.append({
     "family":fam["name"],"test":"ROLLING","hold_trading_days":hold,"n":len(xs),
     "positive_pct":100*sum(x>0 for x in xs)/len(xs),"median_return_im_pct":statistics.median(xs),
     "mean_return_im_pct":statistics.fmean(xs),"worst":min(xs),"best":max(xs)
    })
  xs=[r for r in expiry if r["family"]==fam["name"]]
  for th in ENTRY_RATIO_THRESHOLDS:
   ys=[r["return_on_im_pct"] for r in xs if r.get("swaprate_ratio") is not None and r["swaprate_ratio"]>=th]
   if ys:
    summary.append({
     "family":fam["name"],"test":"SEP_EXPIRY","ratio_threshold":th,"n":len(ys),
     "positive_pct":100*sum(x>0 for x in ys)/len(ys),"median_return_im_pct":statistics.median(ys),
     "mean_return_im_pct":statistics.fmean(ys),"worst":min(ys),"best":max(ys)
    })

 (OUT/"moex_crypto_curve_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"current":current,"summary":summary},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
