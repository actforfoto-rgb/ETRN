from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=30);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
 try:return float(v)
 except:return None

def compact_security(s):
 keys=[
  "SECID","SHORTNAME","NAME","ASSETCODE","SECTYPE","LOTSIZE","LOTVOLUME",
  "LASTTRADEDATE","LASTDELDATE","DELIVERYBASIS","SETTLEPRICE",
  "MINSTEP","STEPPRICE","INITIALMARGIN","BUYSELLFEE","SCALPERFEE",
  "PREVSETTLEPRICE","DECIMALS"
 ]
 out={k:s.get(k) for k in keys if k in s}
 # Also expose any useful relationship fields not known ahead of time.
 for k,v in s.items():
  ku=k.upper()
  if any(tag in ku for tag in ("UNDER","BASE","ASSET","CODE","NAME")) and k not in out:
   out[k]=v
 return out

def main():
 j=get(f"{BASE}/engines/futures/markets/forts/securities.json",{
   "iss.meta":"off","iss.only":"securities,marketdata"
 })
 sec=rows(j,"securities")
 md={r.get("SECID"):r for r in rows(j,"marketdata")}
 perps=[];fixed=[]
 for s in sec:
  sid=s.get("SECID");exp=s.get("LASTTRADEDATE")
  if not sid:continue
  m=md.get(sid,{})
  sw=num(m.get("SWAPRATE") or m.get("SWAPRATE_CURR"))
  try:ed=date.fromisoformat(exp)
  except:ed=None
  row=compact_security(s)
  row.update({
   "BID":num(m.get("BID")),"OFFER":num(m.get("OFFER")),
   "LAST":num(m.get("LAST")),"SWAPRATE":sw,
   "NUMTRADES":num(m.get("NUMTRADES")) or 0,
   "VOLUME":num(m.get("VOLTODAY")) or 0,
   "OPENPOSITION":num(m.get("OPENPOSITION")) or 0
  })
  if ed and ed.year>=2099 and sw is not None:
   perps.append(row)
  elif ed and TODAY<ed and ed.year<2099:
   fixed.append(row)

 # Search results for roots of active perps to expose naming links.
 searches={}
 for p in perps:
  if p["NUMTRADES"]<5:continue
  sid=p["SECID"]
  terms={sid, sid[:-1] if sid.endswith("F") else sid}
  sn=str(p.get("SHORTNAME") or "")
  if sn:terms.add(sn)
  results=[]
  for term in sorted(t for t in terms if len(t)>=2):
   try:
    sj=get(f"{BASE}/securities.json",{"q":term,"iss.meta":"off"})
    rr=rows(sj,"securities")[:30]
    results.extend(rr)
   except Exception:
    pass
  # unique compact results
  seen=set();compact=[]
  for r in results:
   rid=r.get("secid") or r.get("SECID")
   if not rid or rid in seen:continue
   seen.add(rid)
   compact.append(r)
  searches[sid]=compact[:50]

 report={
  "security_columns":list(sec[0].keys()) if sec else [],
  "perpetuals":perps,
  "fixed_active_count":len(fixed),
  "fixed_active":fixed,
  "searches":searches
 }
 (OUT/"moex_perpetual_metadata_probe.json").write_text(
  json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"
 )
 print(json.dumps({
  "security_columns":report["security_columns"],
  "perpetual_count":len(perps),
  "active_fixed_count":len(fixed),
  "perpetuals":perps
 },ensure_ascii=False,indent=2))

if __name__=="__main__":main()
