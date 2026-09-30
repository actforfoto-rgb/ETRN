from __future__ import annotations

import json, math, re
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-OPTION-PARITY-SCANNER/1.0"})
MIN_NET_RUB=2.0
UNIT_TOL=0.05
EXTRA_TICK_MULT=2.0

PAT=re.compile(r".*-[0-9.]+([MP])\d{6}([CP])([AE]).*")

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=35);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def unit(s):
 ms=num(s.get("MINSTEP")) or 1.0
 sp=num(s.get("STEPPRICE")) or ms
 return sp/ms

def main():
 oj=get(f"{BASE}/engines/futures/markets/options/boards/ROPD/securities.json",
        {"iss.meta":"off","iss.only":"securities,marketdata"})
 options=rows(oj,"securities");omd={r.get("SECID"):r for r in rows(oj,"marketdata")}

 fj=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities.json",
        {"iss.meta":"off","iss.only":"securities,marketdata"})
 fsec={r.get("SECID"):r for r in rows(fj,"securities")}
 fmd={r.get("SECID"):r for r in rows(fj,"marketdata")}

 groups={}
 skipped={"no_style_parse":0,"not_futures_style":0,"no_quotes":0}
 for s in options:
  sid=s.get("SECID");short=s.get("SHORTNAME") or ""
  m=PAT.match(short)
  if not m:
   skipped["no_style_parse"]+=1;continue
  settlement,optchar,exercise=m.groups()
  if settlement!="M":
   skipped["not_futures_style"]+=1;continue
  md=omd.get(sid,{})
  bid=num(md.get("BID"));ask=num(md.get("OFFER"))
  if bid is None or ask is None or bid<=0 or ask<=0:
   skipped["no_quotes"]+=1;continue
  und=s.get("UNDERLYINGASSET");strike=num(s.get("STRIKE"));exp=s.get("LASTTRADEDATE")
  typ=s.get("OPTIONTYPE") or optchar
  if not und or strike is None or not exp or typ not in ("C","P"):continue
  key=(und,exp,strike,exercise)
  groups.setdefault(key,{})[typ]=(s,md)

 out=[]
 for (und,exp,strike,exercise),g in groups.items():
  if "C" not in g or "P" not in g:continue
  fs=fsec.get(und);fm=fmd.get(und)
  if not fs or not fm:continue
  fb=num(fm.get("BID"));fa=num(fm.get("OFFER"))
  if fb is None or fa is None or fb<=0 or fa<=0:continue
  cs,cm=g["C"];ps,pm=g["P"]
  cb,ca=num(cm["BID"]),num(cm["OFFER"]);pb,pa=num(pm["BID"]),num(pm["OFFER"])
  cu,pu,fu=unit(cs),unit(ps),unit(fs)
  umax=max(cu,pu,fu);umin=min(cu,pu,fu)
  if umin<=0 or (umax-umin)/umax>UNIT_TOL:continue
  u=statistics_unit=(cu+pu+fu)/3

  # executable synthetic forward bounds
  synth_long_ask=strike+ca-pb  # long call at ask, short put at bid
  synth_short_bid=strike+cb-pa # short call at bid, long put at ask

  # Case A: synthetic forward cheap -> long synthetic, short actual future
  gross_a=(fb-synth_long_ask)*u
  # Case B: synthetic forward rich -> short synthetic, long actual future
  gross_b=(synth_short_bid-fa)*u

  fees=2*((num(cs.get("BUYSELLFEE")) or 0)+(num(ps.get("BUYSELLFEE")) or 0)+(num(fs.get("BUYSELLFEE")) or 0))
  exercise_fees=(num(cs.get("EXERCISEFEE")) or 0)+(num(ps.get("EXERCISEFEE")) or 0)
  tick_buffer=EXTRA_TICK_MULT*((num(cs.get("STEPPRICE")) or 0)+(num(ps.get("STEPPRICE")) or 0)+(num(fs.get("STEPPRICE")) or 0))
  costs=fees+exercise_fees+tick_buffer

  if gross_a>=gross_b:
   direction="LONG_SYNTH_SHORT_FUT"
   gross=gross_a
   implied=synth_long_ask
  else:
   direction="SHORT_SYNTH_LONG_FUT"
   gross=gross_b
   implied=synth_short_bid
  net=gross-costs
  out.append({
   "underlying":und,"expiry":exp,"strike":strike,"exercise_style":exercise,
   "call":cs.get("SECID"),"put":ps.get("SECID"),
   "call_bid":cb,"call_ask":ca,"put_bid":pb,"put_ask":pa,
   "future_bid":fb,"future_ask":fa,"synthetic_forward_exec":implied,
   "direction":direction,"gross_edge_rub":gross,"cost_buffer_rub":costs,
   "net_edge_rub":net,"call_trades":num(cm.get("NUMTRADES")) or 0,
   "put_trades":num(pm.get("NUMTRADES")) or 0,
   "call_oi":num(cm.get("OPENPOSITION")) or 0,"put_oi":num(pm.get("OPENPOSITION")) or 0,
   "future_trades":num(fm.get("NUMTRADES")) or 0,
   "status":"PARITY_CANDIDATE" if net>=MIN_NET_RUB else "REJECT"
  })

 out.sort(key=lambda r:r["net_edge_rub"],reverse=True)
 positive=[r for r in out if r["net_edge_rub"]>=MIN_NET_RUB]
 summary={
  "paired_two_sided_strikes":len(out),"positive_candidates":len(positive),
  "american_candidates":sum(r["exercise_style"]=="A" for r in positive),
  "european_candidates":sum(r["exercise_style"]=="E" for r in positive),
  "note":"Futures-style option put-call parity screen. American exercise candidates require separate early-exercise/broker-margin validation before executable status.",
  "top":out[:100],"skipped":skipped
 }
 (OUT/"moex_option_parity_scan.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({"paired":len(out),"positive":len(positive),"top":out[:20]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
