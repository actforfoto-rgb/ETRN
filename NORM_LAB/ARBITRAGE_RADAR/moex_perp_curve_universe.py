from __future__ import annotations

import csv, json, math
from zoneinfo import ZoneInfo
from datetime import date, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()
KEY_RATE=0.14

CONFIGS=[
 {"name":"CNY","perp":"CNYRUBF","quarter_short":"CNY-12.26"},
 {"name":"USD","perp":"USDRUBF","quarter_short":"Si-12.26"},
 {"name":"EUR","perp":"EURRUBF","quarter_short":"Eu-12.26"},
 {"name":"IMOEX","perp":"IMOEXF","quarter_short":"MXI-12.26"},
 {"name":"RGBI","perp":"RGBIF","quarter_short":"RGBI-12.26"},
 {"name":"SBER","perp":"SBERF","quarter_short":"SBRF-12.26"},
 {"name":"GAZP","perp":"GAZPF","quarter_short":"GAZR-12.26"},
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

def search_short(shortname):
 j=get(f"{BASE}/securities.json",{"q":shortname,"iss.meta":"off"})
 hits=rows(j,"securities")
 exact=[x for x in hits if str(x.get("shortname") or x.get("SHORTNAME") or "").upper()==shortname.upper()]
 fut=[x for x in exact if str(x.get("type") or x.get("TYPE") or "").lower()=="futures"]
 return (fut or exact or hits[:1])[0] if hits else None

def current(secid):
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
 s=rows(j,"securities");m=rows(j,"marketdata")
 if not s or not m:return None
 a=s[0];b=m[0]
 minstep=num(a.get("MINSTEP")) or 1.0
 stepprice=num(a.get("STEPPRICE")) or minstep
 unit_rub=stepprice/minstep
 return {
  "secid":secid,"shortname":a.get("SHORTNAME"),"bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
  "last":num(b.get("LAST")),"settle":num(b.get("SETTLEPRICE")),
  "swaprate":num(b.get("SWAPRATE") or b.get("SWAPRATE_CURR")),
  "lot":num(a.get("LOTVOLUME")) or 1.0,"minstep":minstep,"stepprice":stepprice,
  "unit_rub_per_price_unit":unit_rub,
  "im":num(a.get("INITIALMARGIN")) or 0.0,
  "fee":num(a.get("BUYSELLFEE")) or 0.0,
  "scalper":num(a.get("SCALPERFEE")) or 0.0,
  "expiry":a.get("LASTTRADEDATE"),
  "volume":num(b.get("VOLTODAY")) or 0.0,
  "value":num(b.get("VALTODAY")) or 0.0,
  "trades":num(b.get("NUMTRADES")) or 0.0,
  "oi":num(b.get("OPENPOSITION")) or 0.0,
 }

def business_days(a,b):
 n=0;cur=a
 while cur<b:
  cur += timedelta(days=1)
  if cur.weekday()<5:n+=1
 return n

def infer_scale(qmid,pmid):
 # q_normalized = qmid / scale; choose power of ten giving closest current normalized price.
 cands=[0.001,0.01,0.1,1,10,100,1000]
 return min(cands,key=lambda s:abs(math.log(max(1e-12,(qmid/s)/pmid))))

def screen(cfg):
 hit=search_short(cfg["quarter_short"])
 if not hit:return {"name":cfg["name"],"error":"quarter not resolved"}
 qid=hit.get("secid") or hit.get("SECID")
 q=current(qid);p=current(cfg["perp"])
 if not q or not p:return {"name":cfg["name"],"quarter":qid,"error":"current marketdata missing"}
 if None in (q["bid"],q["ask"],p["bid"],p["ask"],p["swaprate"]):
  return {"name":cfg["name"],"quarter":qid,"error":"bid/ask/swaprate missing","q":q,"p":p}

 try: exp=date.fromisoformat(q["expiry"])
 except: return {"name":cfg["name"],"quarter":qid,"error":"bad expiry"}
 qmid=(q["bid"]+q["ask"])/2
 pmid=(p["bid"]+p["ask"])/2
 scale=infer_scale(qmid,pmid)

 # Sensitivity to one normalized underlying-price unit.
 q_sens=q["unit_rub_per_price_unit"]*scale
 p_sens=p["unit_rub_per_price_unit"]
 # Use one perpetual contract, fractional quarterly equivalent for screening.
 q_qty=p_sens/q_sens
 caldays=(exp-TODAY).days
 bdays=business_days(TODAY,exp)

 if p["swaprate"]>=0:
  direction="LONG_QUARTERLY_SHORT_PERP"
  convergence = p["bid"]*p_sens - q["ask"]*q_qty*q["unit_rub_per_price_unit"]
 else:
  direction="SHORT_QUARTERLY_LONG_PERP"
  convergence = q["bid"]*q_qty*q["unit_rub_per_price_unit"] - p["ask"]*p_sens

 funding=abs(p["swaprate"])*p_sens*bdays
 margin=q_qty*q["im"]+p["im"]
 margin_cost=margin*KEY_RATE*caldays/365
 fees=2*(q_qty*q["fee"]+p["fee"])
 # Conservative exit spread cost using today's full bid/ask on both legs.
 exit_spread=(q["ask"]-q["bid"])*q_qty*q["unit_rub_per_price_unit"] + (p["ask"]-p["bid"])*p_sens
 net=convergence+funding-margin_cost-fees-exit_spread
 req=(margin_cost+fees+exit_spread-convergence)/(p_sens*bdays) if bdays>0 else None

 return {
  "name":cfg["name"],"perp":cfg["perp"],"quarter":qid,"quarter_short":cfg["quarter_short"],
  "direction":direction,"expiry":q["expiry"],"calendar_days":caldays,"business_days":bdays,
  "quarter_bid":q["bid"],"quarter_ask":q["ask"],"perp_bid":p["bid"],"perp_ask":p["ask"],
  "quarter_scale_to_perp_units":scale,"quarter_qty_per_1_perp":q_qty,
  "quarter_unit_rub":q["unit_rub_per_price_unit"],"perp_unit_rub":p_sens,
  "swaprate":p["swaprate"],"funding_if_current_swaprate_rub":funding,
  "convergence_pnl_rub":convergence,"combined_im_rub":margin,
  "margin_cost_key14_rub":margin_cost,"roundtrip_exchange_fee_rub":fees,
  "exit_spread_buffer_rub":exit_spread,
  "screen_net_if_current_swaprate_rub":net,
  "screen_return_on_im_pct":net/margin*100 if margin else None,
  "required_avg_abs_swaprate_to_breakeven":req,
  "current_swaprate_over_required":abs(p["swaprate"])/req if req and req>0 else None,
  "perp_volume":p["volume"],"perp_value":p["value"],"perp_oi":p["oi"],
  "quarter_volume":q["volume"],"quarter_value":q["value"],"quarter_oi":q["oi"],
 }

def main():
 out=[]
 for cfg in CONFIGS:
  try:out.append(screen(cfg))
  except Exception as e:out.append({"name":cfg["name"],"error":f"{type(e).__name__}: {e}"})
 out.sort(key=lambda x:x.get("screen_return_on_im_pct") if x.get("screen_return_on_im_pct") is not None else -999,reverse=True)
 (OUT/"moex_perp_curve_universe.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
 good=[x for x in out if "error" not in x]
 if good:
  with (OUT/"moex_perp_curve_universe.csv").open("w",newline="",encoding="utf-8") as f:
   w=csv.DictWriter(f,fieldnames=list(good[0].keys()));w.writeheader();w.writerows(good)
 print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
