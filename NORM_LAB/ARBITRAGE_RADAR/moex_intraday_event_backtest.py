from __future__ import annotations

import json, math, statistics
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)
STATE=ROOT/"production"/"event_state"/"moex_event_state.json"
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-MOEX-INTRADAY-EVENT-BT/1.0"})
MSK=ZoneInfo("Europe/Moscow")

LOOKBACK=60
ENTRY_Z=2.5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_BARS=120
DAYS=5
STOCK_ROUNDTRIP_FEE_BPS=10.0
EXTRA_RUB=2.0

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=30);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def fut_meta(secid):
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
 s=rows(j,"securities");m=rows(j,"marketdata")
 if not s:return None
 a=s[0];b=m[0] if m else {}
 ms=num(a.get("MINSTEP")) or 1.0;sp=num(a.get("STEPPRICE")) or ms
 return {"unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0,
         "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),"im":num(a.get("INITIALMARGIN")) or 0.0}

def stock_meta(secid):
 j=get(f"{BASE}/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json",{"iss.meta":"off"})
 s=rows(j,"securities");m=rows(j,"marketdata")
 if not s:return None
 a=s[0];b=m[0] if m else {}
 return {"bid":num(b.get("BID")),"ask":num(b.get("OFFER")),"lot":num(a.get("LOTSIZE")) or 1.0}

def candles(secid,kind,frm,till):
 if kind=="fut":
  url=f"{BASE}/engines/futures/markets/forts/securities/{secid}/candles.json"
 else:
  url=f"{BASE}/engines/stock/markets/shares/securities/{secid}/candles.json"
 out=[];start=0
 while True:
  j=get(url,{"from":frm,"till":till,"interval":1,"start":start,"iss.meta":"off"})
  rr=rows(j,"candles")
  if not rr:break
  out.extend(rr)
  if len(rr)<500:break
  start+=len(rr)
  if start>12000:break
 return {r.get("begin"):num(r.get("close")) for r in out if r.get("begin") and num(r.get("close")) is not None}

def infer_scale(a,b):
 c=[0.0001,0.001,0.01,0.1,1,10,100,1000,10000]
 return min(c,key=lambda s:abs(math.log(max(1e-12,(b/s)/a))))

def current_price(meta):
 if meta.get("bid") is None or meta.get("ask") is None:return None
 return (meta["bid"]+meta["ask"])/2

def build_spec(key):
 parts=key.split(":");typ=parts[0]
 if typ=="PF":
  _,a,b=parts
  am=fut_meta(a);bm=fut_meta(b)
  if not am or not bm:return None
  ap=current_price(am);bp=current_price(bm)
  if not ap or not bp:return None
  scale=infer_scale(ap,bp)
  bqty=am["unit"]/(bm["unit"]*scale)
  spread=(am["ask"]-am["bid"])*am["unit"]+(bm["ask"]-bm["bid"])*bqty*bm["unit"]
  cost=2*(am["fee"]+bqty*bm["fee"])+2*spread+EXTRA_RUB
  return {"key":key,"type":typ,"a":a,"b":b,"ak":"fut","bk":"fut","scale":scale,
          "unit":am["unit"],"cost":cost,"two_way":True}
 if typ=="FF":
  _,a,b=parts
  am=fut_meta(a);bm=fut_meta(b)
  if not am or not bm:return None
  bqty=am["unit"]/bm["unit"]
  spread=(am["ask"]-am["bid"])*am["unit"]+(bm["ask"]-bm["bid"])*bqty*bm["unit"]
  cost=2*(am["fee"]+bqty*bm["fee"])+2*spread+EXTRA_RUB
  return {"key":key,"type":typ,"a":a,"b":b,"ak":"fut","bk":"fut","scale":1.0,
          "unit":am["unit"],"cost":cost,"two_way":True}
 if typ=="SF":
  _,a,b=parts
  am=stock_meta(a);bm=fut_meta(b)
  if not am or not bm:return None
  ap=current_price(am);bp=current_price(bm)
  if not ap or not bp:return None
  scale=infer_scale(ap,bp)
  exposure=bm["unit"]*scale
  stock_notional=ap*exposure
  spread=(am["ask"]-am["bid"])*exposure+(bm["ask"]-bm["bid"])*bm["unit"]
  cost=stock_notional*STOCK_ROUNDTRIP_FEE_BPS/10000+2*bm["fee"]+2*spread+EXTRA_RUB
  return {"key":key,"type":typ,"a":a,"b":b,"ak":"stock","bk":"fut","scale":scale,
          "unit":exposure,"cost":cost,"two_way":False}
 return None

def relation_value(spec,pa,pb):
 return pb/spec["scale"]-pa

def simulate(spec,A,B):
 ts=sorted(set(A)&set(B))
 hist=deque(maxlen=LOOKBACK)
 pos=None;trades=[]
 for t in ts:
  x=relation_value(spec,A[t],B[t])
  if len(hist)<LOOKBACK:
   hist.append(x);continue
  mean=statistics.fmean(hist)
  sd=statistics.pstdev(hist)
  z=(x-mean)/sd if sd>1e-12 else 0.0

  if pos:
   pos["bars"]+=1
   pnl=((pos["entry_x"]-x) if pos["dir"]=="HIGH" else (x-pos["entry_x"]))*spec["unit"]-spec["cost"]
   reason=None
   if abs(z)<=EXIT_Z:reason="CONVERGENCE"
   elif abs(z)>=STOP_Z:reason="Z_STOP"
   elif pos["bars"]>=MAX_HOLD_BARS:reason="MAX_HOLD"
   if reason:
    trades.append({**pos,"exit":t,"exit_x":x,"exit_z":z,"net_rub":pnl,"reason":reason})
    pos=None
  else:
   if z>=ENTRY_Z:
    expected=(x-mean)*spec["unit"]-spec["cost"]
    if expected>0:
     pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"HIGH","bars":0,"expected_net_rub":expected}
   elif z<=-ENTRY_Z and spec["two_way"]:
    expected=(mean-x)*spec["unit"]-spec["cost"]
    if expected>0:
     pos={"entry":t,"entry_x":x,"entry_z":z,"dir":"LOW","bars":0,"expected_net_rub":expected}
  hist.append(x)
 return trades,len(ts)

def main():
 if not STATE.exists():
  raise SystemExit("event state not found")
 state=json.loads(STATE.read_text(encoding="utf-8"))
 keys=list(state.get("stats",{}).keys())
 # Prioritize all perp/fixed, then first 12 fixed/fixed, then first 12 spot/fixed.
 pf=[k for k in keys if k.startswith("PF:")]
 ff=[k for k in keys if k.startswith("FF:")][:12]
 sf=[k for k in keys if k.startswith("SF:")][:12]
 keys=pf+ff+sf

 now=datetime.now(MSK)
 frm=(now-timedelta(days=DAYS)).strftime("%Y-%m-%d")
 till=now.strftime("%Y-%m-%d")
 cache={};reports=[];alltrades=[]

 for key in keys:
  try:
   spec=build_spec(key)
   if not spec:continue
   for secid,kind in ((spec["a"],spec["ak"]),(spec["b"],spec["bk"])):
    ck=(secid,kind)
    if ck not in cache:cache[ck]=candles(secid,kind,frm,till)
   tr,n=simulate(spec,cache[(spec["a"],spec["ak"])],cache[(spec["b"],spec["bk"])])
   for x in tr:x.update({"key":key,"type":spec["type"]})
   alltrades.extend(tr)
   nets=[x["net_rub"] for x in tr]
   reports.append({
    "key":key,"type":spec["type"],"bars":n,"trades":len(tr),
    "positive_pct":100*sum(x>0 for x in nets)/len(nets) if nets else None,
    "median_net_rub":statistics.median(nets) if nets else None,
    "aggregate_net_rub":sum(nets) if nets else 0.0,
    "best_net_rub":max(nets) if nets else None,"worst_net_rub":min(nets) if nets else None,
    "cost_proxy_rub":spec["cost"]
   })
  except Exception as e:
   reports.append({"key":key,"error":f"{type(e).__name__}: {e}"[:600]})

 reports.sort(key=lambda r:(r.get("aggregate_net_rub") or -1e99),reverse=True)
 (OUT/"moex_intraday_event_summary.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
 (OUT/"moex_intraday_event_trades.json").write_text(json.dumps(alltrades,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps(reports[:30],ensure_ascii=False,indent=2))

if __name__=="__main__":main()
