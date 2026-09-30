from __future__ import annotations

import json, math
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
SRC=OUT/"moex_calendar_risk_shortlist.json"
DEST=OUT/"moex_calendar_capital_economics.json"

BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-CALENDAR-CAPITAL/1.0"})

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
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s:return None
    a=s[0];b=m[0] if m else {}
    return {
      "secid":secid,
      "initialmargin":num(a.get("INITIALMARGIN")) or 0,
      "buysellfee":num(a.get("BUYSELLFEE")) or 0,
      "scalperfee":num(a.get("SCALPERFEE")) or 0,
      "stepprice":num(a.get("STEPPRICE")),
      "minstep":num(a.get("MINSTEP")),
      "volume":num(b.get("VOLTODAY")) or 0,
      "trades":num(b.get("NUMTRADES")) or 0,
      "oi":num(b.get("OPENPOSITION")) or 0
    }

def main():
    src=json.loads(SRC.read_text(encoding="utf-8"))
    out=[]
    for r in src.get("robust_routes",[]):
        try:
            n=meta(r["near"]);f=meta(r["far"])
        except Exception as e:
            out.append({"spread":r["spread"],"error":f"{type(e).__name__}: {e}"})
            continue
        if not n or not f:continue
        cap=n["initialmargin"]+f["initialmargin"]
        h=r.get("holdout") or {}
        med=float(h.get("median_net_rub") or 0)
        agg=float(h.get("aggregate_net_rub") or 0)
        worst=float(h.get("worst_net_rub") or 0)
        events=int(h.get("n") or 0)
        x={
          "spread":r["spread"],"near":n,"far":f,
          "conservative_sum_leg_im_rub":cap,
          "holdout_events":events,
          "holdout_positive_pct":h.get("positive_pct"),
          "median_net_rub":med,"aggregate_net_rub":agg,"worst_net_rub":worst,
          "median_event_return_on_sum_im_pct":(med/cap*100 if cap>0 else None),
          "aggregate_holdout_return_on_sum_im_pct":(agg/cap*100 if cap>0 else None),
          "worst_event_return_on_sum_im_pct":(worst/cap*100 if cap>0 else None),
          "risk_adjusted_score":(r.get("risk") or {}).get("risk_adjusted_score"),
          "note":"Capital denominator is conservative sum of current leg IM; actual exchange calendar-spread margin may differ and is not assumed."
        }
        out.append(x)
    out.sort(key=lambda x:(x.get("median_event_return_on_sum_im_pct") or -1e99,
                           x.get("aggregate_holdout_return_on_sum_im_pct") or -1e99),reverse=True)
    report={"routes":out,"count":len(out)}
    DEST.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
