from __future__ import annotations

import csv, json, math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT=Path(__file__).resolve().parent
SHADOW=ROOT/"shadow_state"
PAPER=ROOT/"paper_state"
PAPER.mkdir(parents=True,exist_ok=True)

PROMOTION=ROOT/"promotion_status.json"
REGISTRY=ROOT/"strategy_registry.json"
STATE=PAPER/"moex_imoex_state.json"
TS=PAPER/"moex_imoex_timeseries.csv"

BASE="https://iss.moex.com/iss"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-PAPER/1.0"})
PERP="IMOEXF"
PREFIX="MM"
MONTH_CODE={3:"H",6:"M",9:"U",12:"Z"}
KEY_RATE=0.14
STOP_MARK_RETURN_IM_PCT=-5.0

FIELDS=[
 "utc","event","quarter","perp","direction","entry_date",
 "quarter_entry","perp_entry","quarter_mark_bid","perp_mark_ask",
 "funding_rub","dividend_adjustment_rub","gross_pair_pnl_rub","fees_rub","capital_cost_rub",
 "net_pnl_rub","net_return_on_im_pct","combined_im_rub",
 "swaprate","ratio","screen_return_on_im_pct","risk_flag"
]

def now_msk():
 return datetime.now(ZoneInfo("Europe/Moscow"))

def utc():
 return datetime.now(ZoneInfo("UTC")).isoformat()

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=20);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def meta(secid):
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
 ss=rows(j,"securities");mm=rows(j,"marketdata")
 if not ss or not mm:return None
 a=ss[0];b=mm[0]
 ms=num(a.get("MINSTEP")) or 1.0
 sp=num(a.get("STEPPRICE")) or ms
 return {
  "secid":secid,"bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
  "swaprate":num(b.get("SWAPRATE") or b.get("SWAPRATE_CURR")),
  "expiry":a.get("LASTTRADEDATE"),"unit":sp/ms,
  "im":num(a.get("INITIALMARGIN")) or 0.0,
  "fee":num(a.get("BUYSELLFEE")) or 0.0,
  "trades":num(b.get("NUMTRADES")) or 0.0,
  "volume":num(b.get("VOLTODAY")) or 0.0
 }

def next_quarter():
 today=now_msk().date()
 candidates=[]
 for y in (today.year,today.year+1):
  for m in (3,6,9,12):
   sid=f"{PREFIX}{MONTH_CODE[m]}{str(y)[-1]}"
   try:q=meta(sid)
   except Exception:continue
   if not q or not q["expiry"]:continue
   try:e=date.fromisoformat(q["expiry"])
   except:continue
   if e>today and q["bid"] is not None and q["ask"] is not None:
    candidates.append((e,q))
 if not candidates:return None
 candidates.sort(key=lambda x:x[0])
 return candidates[0][1]

def business_days(a,b):
 n=0;cur=a
 while cur<b:
  cur+=timedelta(days=1)
  if cur.weekday()<5:n+=1
 return n

def calc_screen(q,p):
 today=now_msk().date()
 exp=date.fromisoformat(q["expiry"])
 days=(exp-today).days
 work=business_days(today,exp)
 if days<=0 or work<=0:return None
 # IMOEX fixed and perpetual both have 10 RUB per index point currently.
 qty=p["unit"]/q["unit"]
 margin=qty*q["im"]+p["im"]
 fees=2*(qty*q["fee"]+p["fee"])
 spreadbuf=(q["ask"]-q["bid"])*qty*q["unit"]+(p["ask"]-p["bid"])*p["unit"]
 carry=margin*KEY_RATE*days/365
 sw=p["swaprate"] or 0.0
 if sw>=0:
  direction="LONG_QUARTERLY_SHORT_PERP"
  convergence=p["bid"]*p["unit"]-q["ask"]*qty*q["unit"]
 else:
  direction="SHORT_QUARTERLY_LONG_PERP"
  convergence=q["bid"]*qty*q["unit"]-p["ask"]*p["unit"]
 funding=abs(sw)*p["unit"]*work
 net=convergence+funding-carry-fees-spreadbuf
 required=(carry+fees+spreadbuf-convergence)/(p["unit"]*work)
 ratio=abs(sw)/required if required>0 else 99.0
 return {
  "direction":direction,"qty":qty,"combined_im":margin,
  "fees_roundtrip":fees,"spreadbuf":spreadbuf,
  "screen_net":net,"screen_return":net/margin*100 if margin else None,
  "required":required,"ratio":ratio,"days":days
 }

def research_rule():
 reg=json.loads(REGISTRY.read_text(encoding="utf-8"))
 s=next(x for x in reg["strategies"] if x.get("id")=="MOEX_IMOEX_PERP_CURVE")
 return s.get("research_rule") or {}

def paper_allowed():
 if not PROMOTION.exists():return False
 p=json.loads(PROMOTION.read_text(encoding="utf-8"))
 return (p.get("moex",{}).get("IMOEX",{}).get("status")=="PAPER_ELIGIBLE")

def load():
 if STATE.exists():
  try:return json.loads(STATE.read_text(encoding="utf-8"))
  except:pass
 return {"position":None,"last_run":None}

def save(st):
 st["last_run"]=utc()
 STATE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def hist_swaps(frm):
 j=get(f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/securities/{PERP}.json",
       {"from":frm,"till":now_msk().date().isoformat(),"iss.meta":"off"})
 return rows(j,"history")

def hist_dividends(frm):
 j=get(f"{BASE}/history/engines/stock/markets/index/securities/IMOEXDIV.json",
       {"from":frm,"till":now_msk().date().isoformat(),"iss.meta":"off"})
 return rows(j,"history")

def carry_since(pos,punit):
 funding_applied=set(pos.get("funding_dates_applied") or [])
 div_applied=set(pos.get("dividend_dates_applied") or [])
 funding_total=float(pos.get("funding_rub") or 0.0)
 dividend_total=float(pos.get("dividend_adjustment_rub") or 0.0)
 new=[]

 # LONG_QUARTERLY_SHORT_PERP -> short perpetual receives +SwapRate.
 # Opposite direction -> long perpetual pays SwapRate.
 funding_sign=1.0 if pos["direction"]=="LONG_QUARTERLY_SHORT_PERP" else -1.0
 for r in hist_swaps(pos["entry_date"]):
  d=r.get("TRADEDATE")
  if not d or d<=pos["entry_date"] or d in funding_applied:continue
  sw=num(r.get("SWAPRATE") or r.get("SWAPRATE_CURR"))
  if sw is None:continue
  pnl=funding_sign*sw*punit
  funding_total += pnl
  funding_applied.add(d)
  new.append({"date":d,"kind":"SWAPRATE","value":sw,"pnl":pnl})

 # Short perpetual pays IMOEXDIV; long perpetual receives it.
 dividend_sign=-1.0 if pos["direction"]=="LONG_QUARTERLY_SHORT_PERP" else 1.0
 for r in hist_dividends(pos["entry_date"]):
  d=r.get("TRADEDATE")
  if not d or d<=pos["entry_date"] or d in div_applied:continue
  dv=num(r.get("CLOSE"))
  if dv is None:continue
  pnl=dividend_sign*dv*punit
  dividend_total += pnl
  div_applied.add(d)
  if abs(dv)>1e-12:
   new.append({"date":d,"kind":"IMOEXDIV","value":dv,"pnl":pnl})

 return (
  funding_total, sorted(funding_applied),
  dividend_total, sorted(div_applied), new
 )

def append(row):
 new=not TS.exists()
 with TS.open("a",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=FIELDS)
  if new:w.writeheader()
  w.writerow({k:row.get(k,"") for k in FIELDS})

def main():
 st=load()
 q=next_quarter();p=meta(PERP)
 if not q or not p:
  save(st);print(json.dumps({"status":"NO_MARKET_DATA"}));return
 screen=calc_screen(q,p)
 if not screen:
  save(st);print(json.dumps({"status":"NO_SCREEN"}));return

 rule=research_rule()
 rule_ok=(
  screen["ratio"]>=float(rule.get("min_swaprate_to_breakeven_ratio",1.25))
  and screen["screen_return"]>=float(rule.get("min_screen_return_on_im_pct",2.0))
 )

 pos=st.get("position")
 event="MARK"

 if pos is None:
  if not paper_allowed():
   save(st)
   print(json.dumps({
    "status":"WAIT_GATE","quarter":q["secid"],"ratio":screen["ratio"],
    "screen_return_on_im_pct":screen["screen_return"],
    "rule_ok":rule_ok
   },ensure_ascii=False,indent=2))
   return
  if not rule_ok:
   save(st)
   print(json.dumps({"status":"WAIT_SIGNAL","ratio":screen["ratio"],
                     "screen_return_on_im_pct":screen["screen_return"]},indent=2))
   return

  pos={
   "quarter":q["secid"],"perp":PERP,"direction":screen["direction"],
   "qty":screen["qty"],"entry_date":now_msk().date().isoformat(),
   "entry_utc":utc(),
   "quarter_entry":q["ask"] if screen["direction"]=="LONG_QUARTERLY_SHORT_PERP" else q["bid"],
   "perp_entry":p["bid"] if screen["direction"]=="LONG_QUARTERLY_SHORT_PERP" else p["ask"],
   "combined_im":screen["combined_im"],"fees_roundtrip":screen["fees_roundtrip"],
   "funding_rub":0.0,"funding_dates_applied":[],
   "dividend_adjustment_rub":0.0,"dividend_dates_applied":[],
   "status":"OPEN"
  }
  st["position"]=pos
  event="OPEN"

 funding,f_applied,dividend,d_applied,newcarry=carry_since(pos,p["unit"])
 pos["funding_rub"]=funding
 pos["funding_dates_applied"]=f_applied
 pos["dividend_adjustment_rub"]=dividend
 pos["dividend_dates_applied"]=d_applied

 if pos["direction"]=="LONG_QUARTERLY_SHORT_PERP":
  q_pnl=(q["bid"]-pos["quarter_entry"])*q["unit"]*pos["qty"]
  p_pnl=(pos["perp_entry"]-p["ask"])*p["unit"]
 else:
  q_pnl=(pos["quarter_entry"]-q["ask"])*q["unit"]*pos["qty"]
  p_pnl=(p["bid"]-pos["perp_entry"])*p["unit"]

 gross=q_pnl+p_pnl+funding+dividend
 held=max(0,(now_msk().date()-date.fromisoformat(pos["entry_date"])).days)
 carry=pos["combined_im"]*KEY_RATE*held/365
 net=gross-pos["fees_roundtrip"]-carry
 ret=net/pos["combined_im"]*100 if pos["combined_im"] else 0.0
 risk="STOP_MARK_BREACH" if ret<=STOP_MARK_RETURN_IM_PCT else ""

 append({
  "utc":utc(),"event":event,"quarter":pos["quarter"],"perp":PERP,
  "direction":pos["direction"],"entry_date":pos["entry_date"],
  "quarter_entry":pos["quarter_entry"],"perp_entry":pos["perp_entry"],
  "quarter_mark_bid":q["bid"],"perp_mark_ask":p["ask"],
  "funding_rub":funding,"dividend_adjustment_rub":dividend,
  "gross_pair_pnl_rub":gross,
  "fees_rub":pos["fees_roundtrip"],"capital_cost_rub":carry,
  "net_pnl_rub":net,"net_return_on_im_pct":ret,
  "combined_im_rub":pos["combined_im"],"swaprate":p["swaprate"],
  "ratio":screen["ratio"],"screen_return_on_im_pct":screen["screen_return"],
  "risk_flag":risk
 })

 pos["last_mark"]={"utc":utc(),"net_rub":net,"return_im_pct":ret,"risk":risk}
 st["position"]=pos
 save(st)

 print(json.dumps({
  "status":"PAPER_OPEN" if pos else "IDLE",
  "position":pos,"new_carry_cashflows":newcarry,
  "mark_net_rub":net,"mark_return_im_pct":ret,
  "screen_ratio":screen["ratio"],"screen_return_im_pct":screen["screen_return"]
 },ensure_ascii=False,indent=2))

if __name__=="__main__":
 main()
