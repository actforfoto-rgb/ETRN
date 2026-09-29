from __future__ import annotations

import csv, json, math
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"shadow_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
CSV_PATH=STATE_DIR/"moex_shadow_timeseries.csv"
LAST_PATH=STATE_DIR/"moex_last_scan.json"
STATE_PATH=STATE_DIR/"moex_state.json"

BASE="https://iss.moex.com/iss"
S=requests.Session(); S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR-SHADOW/1.0"})
TODAY=date(2026,9,30)
KEY_RATE=0.14

CONFIGS=[
 {"name":"CNY","perp":"CNYRUBF","quarter":"CRZ6"},
 {"name":"USD","perp":"USDRUBF","quarter":"SiZ6"},
 {"name":"EUR","perp":"EURRUBF","quarter":"EuZ6"},
 {"name":"IMOEX","perp":"IMOEXF","quarter":"MMZ6"},
 {"name":"RGBI","perp":"RGBIF","quarter":"RBZ6"},
]

FIELDS=[
 "utc","name","perp","quarter","direction","perp_bid","perp_ask","quarter_bid","quarter_ask",
 "swaprate","required_swaprate","swaprate_ratio","calendar_days","business_days",
 "combined_im_rub","screen_net_rub","screen_return_on_im_pct",
 "perp_volume","quarter_volume","perp_oi","quarter_oi","status"
]

def utc(): return datetime.now(timezone.utc).isoformat()

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=20);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def current(secid):
    j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s or not m:return None
    a=s[0];b=m[0]
    ms=num(a.get("MINSTEP")) or 1
    sp=num(a.get("STEPPRICE")) or ms
    return {
      "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
      "swaprate":num(b.get("SWAPRATE") or b.get("SWAPRATE_CURR")),
      "expiry":a.get("LASTTRADEDATE"),
      "im":num(a.get("INITIALMARGIN")) or 0,
      "fee":num(a.get("BUYSELLFEE")) or 0,
      "unit":sp/ms,
      "volume":num(b.get("VOLTODAY")) or 0,
      "trades":num(b.get("NUMTRADES")) or 0,
      "oi":num(b.get("OPENPOSITION")) or 0,
      "systime":b.get("SYSTIME")
    }

def business_days(a,b):
    n=0;cur=a
    while cur<b:
      cur += timedelta(days=1)
      if cur.weekday()<5:n+=1
    return n

def infer_scale(qmid,pmid):
    cands=[0.001,0.01,0.1,1,10,100,1000,10000]
    return min(cands,key=lambda s:abs(math.log(max(1e-12,(qmid/s)/pmid))))

def calc(cfg):
    p=current(cfg["perp"]);q=current(cfg["quarter"])
    if not p or not q:return {"name":cfg["name"],"error":"missing data"}
    if None in (p["bid"],p["ask"],p["swaprate"],q["bid"],q["ask"]):
        return {"name":cfg["name"],"error":"missing bid/ask/swaprate"}
    exp=date.fromisoformat(q["expiry"])
    qmid=(q["bid"]+q["ask"])/2; pmid=(p["bid"]+p["ask"])/2
    scale=infer_scale(qmid,pmid)
    qqty=p["unit"]/(q["unit"]*scale)
    days=(exp-TODAY).days; work=business_days(TODAY,exp)
    if days<=0 or work<=0:return {"name":cfg["name"],"error":"expired"}
    if p["swaprate"]>=0:
        direction="LONG_QUARTERLY_SHORT_PERP"
        convergence=p["bid"]*p["unit"]-q["ask"]*qqty*q["unit"]
    else:
        direction="SHORT_QUARTERLY_LONG_PERP"
        convergence=q["bid"]*qqty*q["unit"]-p["ask"]*p["unit"]
    funding=abs(p["swaprate"])*p["unit"]*work
    margin=qqty*q["im"]+p["im"]
    carry=margin*KEY_RATE*days/365
    fees=2*(qqty*q["fee"]+p["fee"])
    spreadbuf=(q["ask"]-q["bid"])*qqty*q["unit"]+(p["ask"]-p["bid"])*p["unit"]
    net=convergence+funding-carry-fees-spreadbuf
    req=(carry+fees+spreadbuf-convergence)/(p["unit"]*work)
    ratio=abs(p["swaprate"])/req if req>0 else None
    ret=net/margin*100 if margin else None
    status="REJECT"
    if ratio is not None and ratio>=1.25 and ret is not None and ret>=2.0 and p["trades"]>=50 and q["trades"]>=50:
        status="STRONG"
    elif ratio is not None and ratio>=1.05 and ret is not None and ret>0:
        status="WATCH"
    return {
      "utc":utc(),"name":cfg["name"],"perp":cfg["perp"],"quarter":cfg["quarter"],
      "direction":direction,"perp_bid":p["bid"],"perp_ask":p["ask"],
      "quarter_bid":q["bid"],"quarter_ask":q["ask"],"swaprate":p["swaprate"],
      "required_swaprate":req,"swaprate_ratio":ratio,"calendar_days":days,"business_days":work,
      "combined_im_rub":margin,"screen_net_rub":net,"screen_return_on_im_pct":ret,
      "perp_volume":p["volume"],"quarter_volume":q["volume"],"perp_oi":p["oi"],"quarter_oi":q["oi"],
      "status":status
    }

def load_state():
    if STATE_PATH.exists():
        try:return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except:pass
    return {"consecutive":{}}

def main():
    st=load_state();out=[]
    for cfg in CONFIGS:
        try:r=calc(cfg)
        except Exception as e:r={"name":cfg["name"],"error":f"{type(e).__name__}: {e}"}
        out.append(r)
        if "error" in r:continue
        name=r["name"]
        c=int(st["consecutive"].get(name,0))
        if r["status"] in ("WATCH","STRONG"):c+=1
        else:c=0
        st["consecutive"][name]=c
        r["consecutive_positive_scans"]=c

    good=[r for r in out if "error" not in r]
    new=not CSV_PATH.exists()
    if good:
        with CSV_PATH.open("a",newline="",encoding="utf-8") as f:
            fields=FIELDS+["consecutive_positive_scans"]
            w=csv.DictWriter(f,fieldnames=fields)
            if new:w.writeheader()
            for r in good:w.writerow({k:r.get(k,"") for k in fields})
    st["last_run"]=utc()
    STATE_PATH.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")
    LAST_PATH.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
