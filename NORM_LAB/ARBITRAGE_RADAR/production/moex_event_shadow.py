from __future__ import annotations

import csv, json, math, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"moex_event_state.json"
SCAN_FILE=STATE_DIR/"moex_event_last_scan.json"
LEDGER_FILE=STATE_DIR/"moex_event_ledger.csv"

BASE="https://iss.moex.com/iss"
MSK=ZoneInfo("Europe/Moscow")
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-EVENT-HUNTER/1.0"})

ALPHA=0.12
MIN_BASELINE_SCANS=12
ENTRY_Z=2.5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_MIN=360
MIN_TRADES=20
MIN_NET_RUB=2.0
MIN_NET_ON_IM_BPS=5.0
STOCK_ROUNDTRIP_FEE_BPS=10.0
EXTRA_SLIPPAGE_RUB=2.0

PERP_FIXED={
 "CNYRUBF":"CRZ6","USDRUBF":"SiZ6","EURRUBF":"EuZ6","IMOEXF":"MMZ6","RGBIF":"RBZ6",
 "SBERF":"SRZ6","GAZPF":"GZZ6","BTCUSDF":"BTV6","ETHUSDF":"EHV6","XRPUSDF":"XRV6",
 "SOLUSDF":"S3V6","TRXUSDF":"TXV6"
}

ASSET_TO_SPOT={
 "SBRF":"SBER","SBPR":"SBERP","GAZR":"GAZP","GMKN":"GMKN","LKOH":"LKOH","ROSN":"ROSN",
 "YDEX":"YDEX","VTBR":"VTBR","NOTK":"NVTK","PLZL":"PLZL","FEES":"FEES","HEAD":"HEAD",
 "SIBN":"SIBN","SOFL":"SOFL","AFLT":"AFLT","AFKS":"AFKS","TATN":"TATN","TATP":"TATNP",
 "RTKM":"RTKM","RTKMP":"RTKMP","MGNT":"MGNT","MTSS":"MTSS","MOEX":"MOEX","NLMK":"NLMK",
 "CHMF":"CHMF","ALRS":"ALRS","PHOR":"PHOR","RUAL":"RUAL","IRAO":"IRAO","MAGN":"MAGN",
 "FLOT":"FLOT","HYDR":"HYDR","TRNF":"TRNFP"
}

LEDGER_FIELDS=[
 "utc","event","key","type","label","direction","entry_z","exit_z","baseline_mean",
 "entry_spread","exit_spread","expected_net_rub","realized_net_rub","hold_min","reason"
]

def utc():return datetime.now(ZoneInfo("UTC")).isoformat()

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def unit(sec):
 ms=num(sec.get("MINSTEP")) or 1.0
 sp=num(sec.get("STEPPRICE")) or ms
 return sp/ms

def parse_msk(s):
 if not s:return None
 try:return datetime.strptime(s,"%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
 except:return None

def fresh(md):
 dt=parse_msk(md.get("SYSTIME"))
 if not dt:return False
 return abs((datetime.now(MSK)-dt).total_seconds())<=20*60

def load_state():
 if STATE_FILE.exists():
  try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
  except:pass
 return {"session":None,"stats":{},"positions":{}}

def save_state(s):
 STATE_FILE.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")

def append_ledger(row):
 new=not LEDGER_FILE.exists()
 with LEDGER_FILE.open("a",newline="",encoding="utf-8") as f:
  w=csv.DictWriter(f,fieldnames=LEDGER_FIELDS)
  if new:w.writeheader()
  w.writerow({k:row.get(k,"") for k in LEDGER_FIELDS})

def infer_scale(a,b):
 c=[0.0001,0.001,0.01,0.1,1,10,100,1000,10000]
 return min(c,key=lambda s:abs(math.log(max(1e-12,(b/s)/a))))

def baseline_before_update(state,key,x,session):
 st=state["stats"].get(key)
 if not st or st.get("session")!=session:
  st={"session":session,"n":0,"mean":x,"var":0.0}
  state["stats"][key]=st
  return st,None
 n=st["n"];mean=st["mean"];var=max(float(st.get("var",0.0)),0.0)
 sd=math.sqrt(var) if n>=MIN_BASELINE_SCANS and var>1e-16 else None
 z=(x-mean)/sd if sd else None
 # update after calculating z: current observation does not define its own anomaly
 delta=x-mean
 new_mean=mean+ALPHA*delta
 new_var=(1-ALPHA)*(var+ALPHA*delta*delta)
 st.update({"n":n+1,"mean":new_mean,"var":new_var})
 return st,z

def seed_baseline_count(state,key):
 st=state["stats"][key]
 if st["n"]==0:st["n"]=1

def futures_snapshot():
 j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities.json",
       {"iss.meta":"off","iss.only":"securities,marketdata"})
 secs={r.get("SECID"):r for r in rows(j,"securities")}
 md={r.get("SECID"):r for r in rows(j,"marketdata")}
 return secs,md

def stock_snapshot():
 j=get(f"{BASE}/engines/stock/markets/shares/boards/TQBR/securities.json",
       {"iss.meta":"off","iss.only":"securities,marketdata"})
 secs={r.get("SECID"):r for r in rows(j,"securities")}
 md={r.get("SECID"):r for r in rows(j,"marketdata")}
 return secs,md

def valid_future(sid,secs,md):
 s=secs.get(sid);m=md.get(sid)
 if not s or not m or not fresh(m):return False
 if (num(m.get("NUMTRADES")) or 0)<MIN_TRADES:return False
 return num(m.get("BID")) is not None and num(m.get("OFFER")) is not None

def relation_perp_fixed(perp,fixed,secs,md):
 if not valid_future(perp,secs,md) or not valid_future(fixed,secs,md):return None
 ps,qs=secs[perp],secs[fixed];pm,qm=md[perp],md[fixed]
 pb,pa=num(pm["BID"]),num(pm["OFFER"]);qb,qa=num(qm["BID"]),num(qm["OFFER"])
 p_mid=(pb+pa)/2;q_mid=(qb+qa)/2
 scale=infer_scale(p_mid,q_mid)
 pu=unit(ps);qu=unit(qs)
 qqty=pu/(qu*scale)
 x=q_mid/scale-p_mid
 hi=qb/scale-pa       # long perp / short fixed
 lo=qa/scale-pb       # short perp / long fixed
 fee_rt=2*((num(ps.get("BUYSELLFEE")) or 0)+qqty*(num(qs.get("BUYSELLFEE")) or 0))
 exit_spread=((pa-pb)*pu+(qa-qb)*qqty*qu)
 im=(num(ps.get("INITIALMARGIN")) or 0)+qqty*(num(qs.get("INITIALMARGIN")) or 0)
 return {
  "key":f"PF:{perp}:{fixed}","type":"PERP_FIXED","label":f"{perp}↔{fixed}",
  "x":x,"hi":hi,"lo":lo,"unit_rub":pu,"fee_rt":fee_rt,"exit_spread_rub":exit_spread,
  "im":im,"a_bid":pb,"a_ask":pa,"b_bid":qb,"b_ask":qa,"b_scale":scale,
  "a_unit":pu,"b_unit":qu,"b_qty":qqty
 }

def relation_fixed_fixed(near,far,secs,md):
 if not valid_future(near,secs,md) or not valid_future(far,secs,md):return None
 ns,fs=secs[near],secs[far];nm,fm=md[near],md[far]
 nb,na=num(nm["BID"]),num(nm["OFFER"]);fb,fa=num(fm["BID"]),num(fm["OFFER"])
 nu,fu=unit(ns),unit(fs)
 if nu<=0 or fu<=0:return None
 fqty=nu/fu
 x=(fb+fa)/2-(nb+na)/2
 hi=fb-na          # long near / short far
 lo=fa-nb          # short near / long far
 fee_rt=2*((num(ns.get("BUYSELLFEE")) or 0)+fqty*(num(fs.get("BUYSELLFEE")) or 0))
 exit_spread=(na-nb)*nu+(fa-fb)*fqty*fu
 im=(num(ns.get("INITIALMARGIN")) or 0)+fqty*(num(fs.get("INITIALMARGIN")) or 0)
 return {
  "key":f"FF:{near}:{far}","type":"FIXED_FIXED","label":f"{near}↔{far}",
  "x":x,"hi":hi,"lo":lo,"unit_rub":nu,"fee_rt":fee_rt,"exit_spread_rub":exit_spread,
  "im":im,"a_bid":nb,"a_ask":na,"b_bid":fb,"b_ask":fa,"b_scale":1.0,
  "a_unit":nu,"b_unit":fu,"b_qty":fqty
 }

def relation_spot_fixed(spot,fixed,ssecs,smd,fsecs,fmd):
 if not valid_future(fixed,fsecs,fmd):return None
 ss=ssecs.get(spot);sm=smd.get(spot);fs=fsecs.get(fixed);fm=fmd.get(fixed)
 if not ss or not sm or not fresh(sm):return None
 if (num(sm.get("NUMTRADES")) or 0)<MIN_TRADES:return None
 sb,sa=num(sm.get("BID")),num(sm.get("OFFER"));fb,fa=num(fm.get("BID")),num(fm.get("OFFER"))
 if None in (sb,sa,fb,fa):return None
 scale=infer_scale((sb+sa)/2,(fb+fa)/2)
 fu=unit(fs)
 exposure=fu*scale  # rub P&L per 1 RUB move of underlying; usually number of shares
 if exposure<=0:return None
 x=(fb+fa)/(2*scale)-(sb+sa)/2
 hi=fb/scale-sa  # only actionable direction: long spot / short future
 stock_notional=sa*exposure
 fee_rt=stock_notional*STOCK_ROUNDTRIP_FEE_BPS/10000 + 2*(num(fs.get("BUYSELLFEE")) or 0)
 exit_spread=(sa-sb)*exposure+(fa-fb)*fu
 return {
  "key":f"SF:{spot}:{fixed}","type":"SPOT_FIXED","label":f"{spot}↔{fixed}",
  "x":x,"hi":hi,"lo":None,"unit_rub":exposure,"fee_rt":fee_rt,"exit_spread_rub":exit_spread,
  "im":stock_notional+(num(fs.get("INITIALMARGIN")) or 0),
  "a_bid":sb,"a_ask":sa,"b_bid":fb,"b_ask":fa,"b_scale":scale,
  "a_unit":exposure,"b_unit":fu,"b_qty":1.0
 }

def build_relations():
 fsecs,fmd=futures_snapshot();ssecs,smd=stock_snapshot()
 rel=[]

 for p,q in PERP_FIXED.items():
  r=relation_perp_fixed(p,q,fsecs,fmd)
  if r:rel.append(r)

 groups={}
 today=datetime.now(MSK).date()
 for sid,s in fsecs.items():
  try:exp=datetime.strptime(s.get("LASTTRADEDATE") or "","%Y-%m-%d").date()
  except:continue
  if not(today<exp and exp.year<2099):continue
  if not valid_future(sid,fsecs,fmd):continue
  asset=s.get("ASSETCODE")
  if asset:groups.setdefault(asset,[]).append((exp,sid))
 for asset,arr in groups.items():
  arr.sort()
  for (_,a),(_,b) in zip(arr,arr[1:]):
   r=relation_fixed_fixed(a,b,fsecs,fmd)
   if r:rel.append(r)

 # Nearest liquid equity future vs spot, cash-and-carry direction only.
 for asset,spot in ASSET_TO_SPOT.items():
  arr=groups.get(asset,[])
  if not arr:continue
  fixed=arr[0][1]
  r=relation_spot_fixed(spot,fixed,ssecs,smd,fsecs,fmd)
  if r:rel.append(r)
 return rel

def mark_pnl(pos,r):
 direction=pos["direction"]
 if direction=="LONG_A_SHORT_B":
  a_exit=r["a_bid"];b_exit=r["b_ask"]
  pnl=(a_exit-pos["a_entry"])*r["a_unit"] + (pos["b_entry"]-b_exit)*r["b_qty"]*r["b_unit"]
 elif direction=="SHORT_A_LONG_B":
  a_exit=r["a_ask"];b_exit=r["b_bid"]
  pnl=(pos["a_entry"]-a_exit)*r["a_unit"] + (b_exit-pos["b_entry"])*r["b_qty"]*r["b_unit"]
 elif direction=="LONG_SPOT_SHORT_FUT":
  a_exit=r["a_bid"];b_exit=r["b_ask"]
  pnl=(a_exit-pos["a_entry"])*r["a_unit"] + (pos["b_entry"]-b_exit)*r["b_unit"]
 else:return None
 return pnl-pos["fee_rt"]-EXTRA_SLIPPAGE_RUB

def main():
 state=load_state()
 session=datetime.now(MSK).date().isoformat()
 if state.get("session")!=session:
  state["session"]=session
  state["stats"]={}
  # Never carry event trades overnight in shadow evaluation.
  state["positions"]={}

 rels=build_relations()
 current={r["key"]:r for r in rels}
 scans=[];opened=[];closed=[]

 # evaluate existing positions before updating baseline
 for key,pos in list(state["positions"].items()):
  r=current.get(key)
  if not r:continue
  st=state["stats"].get(key)
  sd=math.sqrt(max(st.get("var",0),0)) if st and st.get("var",0)>0 else None
  z=(r["x"]-st["mean"])/sd if st and sd else None
  pnl=mark_pnl(pos,r)
  hold=(time.time()-pos["opened_ts"])/60
  reason=None
  if z is not None and abs(z)<=EXIT_Z:reason="CONVERGENCE"
  elif z is not None and abs(z)>=STOP_Z:reason="Z_STOP"
  elif hold>=MAX_HOLD_MIN:reason="MAX_HOLD"
  if reason:
   append_ledger({"utc":utc(),"event":"CLOSE","key":key,"type":pos["type"],"label":pos["label"],
                  "direction":pos["direction"],"entry_z":pos["entry_z"],"exit_z":z,
                  "baseline_mean":st["mean"] if st else None,"entry_spread":pos["entry_spread"],
                  "exit_spread":r["x"],"expected_net_rub":pos["expected_net_rub"],
                  "realized_net_rub":pnl,"hold_min":hold,"reason":reason})
   closed.append({"key":key,"realized_net_rub":pnl,"reason":reason})
   del state["positions"][key]

 for r in rels:
  st,z=baseline_before_update(state,r["key"],r["x"],session)
  seed_baseline_count(state,r["key"])
  mean=st["mean"];sd=math.sqrt(max(st["var"],0)) if st["var"]>0 else None
  expected=None;direction=None;entry_spread=None

  if z is not None and abs(z)>=ENTRY_Z and r["key"] not in state["positions"]:
   costs=r["fee_rt"]+r["exit_spread_rub"]+EXTRA_SLIPPAGE_RUB
   if z>0:
    gross=(r["hi"]-mean)*r["unit_rub"]
    if gross>costs:
     direction="LONG_A_SHORT_B" if r["type"]!="SPOT_FIXED" else "LONG_SPOT_SHORT_FUT"
     entry_spread=r["hi"];expected=gross-costs
   elif z<0 and r["type"]!="SPOT_FIXED":
    gross=(mean-r["lo"])*r["unit_rub"]
    if gross>costs:
     direction="SHORT_A_LONG_B";entry_spread=r["lo"];expected=gross-costs

   if direction and expected is not None:
    ret_bps=expected/max(r["im"],1e-9)*10000
    if expected>=MIN_NET_RUB and ret_bps>=MIN_NET_ON_IM_BPS:
     if direction in ("LONG_A_SHORT_B","LONG_SPOT_SHORT_FUT"):
      a_entry=r["a_ask"];b_entry=r["b_bid"]
     else:
      a_entry=r["a_bid"];b_entry=r["b_ask"]
     pos={"key":r["key"],"type":r["type"],"label":r["label"],"direction":direction,
          "opened_ts":time.time(),"entry_z":z,"entry_spread":entry_spread,
          "a_entry":a_entry,"b_entry":b_entry,"fee_rt":r["fee_rt"],
          "expected_net_rub":expected}
     state["positions"][r["key"]]=pos
     append_ledger({"utc":utc(),"event":"OPEN","key":r["key"],"type":r["type"],"label":r["label"],
                    "direction":direction,"entry_z":z,"baseline_mean":mean,
                    "entry_spread":entry_spread,"expected_net_rub":expected,
                    "reason":"EVENT_DISLOCATION"})
     opened.append(pos)

  scans.append({"key":r["key"],"type":r["type"],"label":r["label"],"mid_spread":r["x"],
                "baseline_mean":mean,"baseline_sd":sd,"z":z,"n":st["n"],
                "fee_rt_rub":r["fee_rt"],"exit_spread_rub":r["exit_spread_rub"],
                "im_rub":r["im"],"open":r["key"] in state["positions"]})

 save_state(state)
 report={"utc":utc(),"session":session,"relations":len(rels),"opened":opened,"closed":closed,
         "open_positions":state["positions"],
         "top_anomalies":sorted([x for x in scans if x["z"] is not None],
                                key=lambda x:abs(x["z"]),reverse=True)[:30]}
 SCAN_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"relations":len(rels),"opened":len(opened),"closed":closed,
                   "top_anomalies":report["top_anomalies"][:10]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
