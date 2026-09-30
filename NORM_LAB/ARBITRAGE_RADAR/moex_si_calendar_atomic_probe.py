from __future__ import annotations

import json, statistics, math
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-SI-CALENDAR-SPREAD/1.0"})

SPREAD="SIZ6SIH7"
NEAR="SiZ6"
FAR="SiH7"

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=25)
    r.raise_for_status()
    return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def sec_current(secid):
    j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities")
    m=rows(j,"marketdata")
    return (s[0] if s else {}),(m[0] if m else {})

def candles(secid,days=10):
    j=get(f"{BASE}/engines/futures/markets/forts/securities/{secid}/candles.json",
          {"interval":1,"from":"2026-09-20","till":"2026-09-30","iss.meta":"off"})
    return rows(j,"candles")

def main():
    ss,sm=sec_current(SPREAD)
    ns,nm=sec_current(NEAR)
    fs,fm=sec_current(FAR)

    nb,na=num(nm.get("BID")),num(nm.get("OFFER"))
    fb,fa=num(fm.get("BID")),num(fm.get("OFFER"))
    sb,sa=num(sm.get("BID")),num(sm.get("OFFER"))

    synthetic={
      "buy_spread_exec": (fa-nb) if None not in (fa,nb) else None,
      "sell_spread_exec": (fb-na) if None not in (fb,na) else None,
      "mid": (((fb+fa)/2)-((nb+na)/2)) if None not in (nb,na,fb,fa) else None
    }

    spread_candles=candles(SPREAD)
    near_candles=candles(NEAR)
    far_candles=candles(FAR)

    sc={x.get("begin"):num(x.get("close")) for x in spread_candles if x.get("begin") and num(x.get("close")) is not None}
    nc={x.get("begin"):num(x.get("close")) for x in near_candles if x.get("begin") and num(x.get("close")) is not None}
    fc={x.get("begin"):num(x.get("close")) for x in far_candles if x.get("begin") and num(x.get("close")) is not None}

    common=sorted(set(sc)&set(nc)&set(fc))
    errors=[]
    diffs=[]
    for t in common:
        synth=fc[t]-nc[t]
        diffs.append(sc[t]-synth)
    if diffs:
        errors={
          "n":len(diffs),
          "median_spread_minus_synthetic":statistics.median(diffs),
          "mean_spread_minus_synthetic":statistics.fmean(diffs),
          "p95_abs":sorted(abs(x) for x in diffs)[round((len(diffs)-1)*.95)],
          "max_abs":max(abs(x) for x in diffs)
        }

    result={
      "spread_security":ss,
      "spread_marketdata":sm,
      "near_marketdata":nm,
      "far_marketdata":fm,
      "synthetic_current":synthetic,
      "spread_bid":sb,
      "spread_ask":sa,
      "atomic_vs_synthetic":{
        "atomic_buy_minus_synthetic_buy": (sa-synthetic["buy_spread_exec"]) if sa is not None and synthetic["buy_spread_exec"] is not None else None,
        "atomic_sell_minus_synthetic_sell": (sb-synthetic["sell_spread_exec"]) if sb is not None and synthetic["sell_spread_exec"] is not None else None
      },
      "spread_candles":len(spread_candles),
      "matched_candles":len(common),
      "spread_vs_synthetic_history":errors
    }
    (OUT/"moex_si_calendar_atomic_probe.json").write_text(
      json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "spread_bid":sb,"spread_ask":sa,
      "synthetic":synthetic,
      "atomic_vs_synthetic":result["atomic_vs_synthetic"],
      "security":{k:ss.get(k) for k in ["SECID","SHORTNAME","INITIALMARGIN","BUYSELLFEE","SCALPERFEE","MINSTEP","STEPPRICE","LASTTRADEDATE"]},
      "history":errors
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
