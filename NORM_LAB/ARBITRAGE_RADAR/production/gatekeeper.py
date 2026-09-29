from __future__ import annotations

import csv, json, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"shadow_state"
OUT=ROOT/"promotion_status.json"

MIN_SHADOW_CLOSED=30
MIN_SHADOW_DAYS=30
MIN_POSITIVE_RATE=0.55
MIN_MEDIAN_NET_BPS=0.0
MIN_AGG_NET_BPS=0.0
MOEX_MIN_STRONG_SCANS=20

def read_csv(path):
    if not path.exists():return []
    with path.open("r",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def crypto_gate():
    rows=read_csv(STATE/"crypto_ledger.csv")
    closes=[r for r in rows if r.get("event")=="CLOSE"]
    nets=[]
    dates=set()
    for r in closes:
        try:nets.append(float(r.get("net_bps") or 0))
        except:pass
        d=(r.get("utc") or "")[:10]
        if d:dates.add(d)
    if not nets:
        return {"status":"SHADOW","closed_cycles":0,"calendar_days":0,
                "reason":"No closed shadow cycles yet"}
    median=statistics.median(nets)
    total=sum(nets)
    positive=sum(x>0 for x in nets)/len(nets)
    eligible=(len(nets)>=MIN_SHADOW_CLOSED and len(dates)>=MIN_SHADOW_DAYS
              and median>MIN_MEDIAN_NET_BPS and total>MIN_AGG_NET_BPS
              and positive>=MIN_POSITIVE_RATE)
    return {
      "status":"PAPER_ELIGIBLE" if eligible else "SHADOW",
      "closed_cycles":len(nets),"calendar_days":len(dates),
      "aggregate_net_bps":total,"median_net_bps":median,
      "positive_cycles_pct":100*positive,
      "requirements":{
        "closed_cycles":MIN_SHADOW_CLOSED,"calendar_days":MIN_SHADOW_DAYS,
        "median_net_bps_gt":MIN_MEDIAN_NET_BPS,
        "aggregate_net_bps_gt":MIN_AGG_NET_BPS,
        "positive_cycles_pct_gte":100*MIN_POSITIVE_RATE
      }
    }

def moex_gate():
    rows=read_csv(STATE/"moex_shadow_timeseries.csv")
    by={}
    dates={}
    for r in rows:
        name=r.get("name")
        if not name:continue
        by.setdefault(name,[])
        dates.setdefault(name,set())
        if r.get("status")=="STRONG":
            by[name].append(r)
        d=(r.get("utc") or "")[:10]
        if d:dates[name].add(d)
    out={}
    for name in sorted(set(list(by)+list(dates))):
        strong=by.get(name,[])
        try:
            returns=[float(r.get("screen_return_on_im_pct") or 0) for r in strong]
            ratios=[float(r.get("swaprate_ratio") or 0) for r in strong if r.get("swaprate_ratio") not in ("",None)]
        except:
            returns=[];ratios=[]
        eligible=(len(strong)>=MOEX_MIN_STRONG_SCANS and len(dates.get(name,set()))>=5
                  and returns and min(returns)>0)
        out[name]={
          "status":"PAPER_ELIGIBLE" if eligible else "SHADOW",
          "strong_scans":len(strong),"calendar_days":len(dates.get(name,set())),
          "median_screen_return_on_im_pct":statistics.median(returns) if returns else None,
          "min_screen_return_on_im_pct":min(returns) if returns else None,
          "median_swaprate_ratio":statistics.median(ratios) if ratios else None,
          "requirements":{"strong_scans":MOEX_MIN_STRONG_SCANS,"calendar_days":5}
        }
    return out

def main():
    result={"crypto":crypto_gate(),"moex":moex_gate(),
            "rule":"Eligibility is not permission for real-money trading. PAPER and MICRO_LIVE require separate enablement."}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
