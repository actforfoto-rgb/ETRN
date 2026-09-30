from __future__ import annotations

import json, math
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
SRC=OUT/"moex_calendar_event_universe_walkforward.json"

MIN_HOLDOUT_N=5
MIN_POSITIVE_PCT=80.0
MIN_CURRENT_TRADES=100
MIN_MEDIAN_RUB=3.0
MAX_LOSS_TO_MEDIAN=2.0
MAX_ROUTES=12

def main():
    raw=json.loads(SRC.read_text(encoding="utf-8"))
    accepted=[];watch=[];rejected=[]
    for r in raw.get("routes",[]):
        h=r.get("holdout") or {}
        med=float(h.get("median_net_rub") or 0)
        worst=float(h.get("worst_net_rub") or 0)
        n=int(h.get("n") or 0)
        pos=float(h.get("positive_pct") or 0)
        agg=float(h.get("aggregate_net_rub") or 0)
        trades=float(r.get("current_trades") or 0)
        loss_ratio=(max(0.0,-worst)/med) if med>0 else 999.0
        base_score=(med*math.sqrt(max(1,n))*(pos/100))
        tail_penalty=1/(1+loss_ratio)
        liquidity_bonus=min(2.0,1+math.log10(max(1,trades))/10)
        score=base_score*tail_penalty*liquidity_bonus
        x={**r,"risk":{
             "loss_to_median_ratio":loss_ratio,
             "risk_adjusted_score":score,
             "holdout_worst_rub":worst
        }}
        hard=(r.get("holdout_pass") and n>=MIN_HOLDOUT_N and pos>=MIN_POSITIVE_PCT
              and med>=MIN_MEDIAN_RUB and agg>0 and trades>=MIN_CURRENT_TRADES
              and loss_ratio<=MAX_LOSS_TO_MEDIAN)
        if hard:
            x["risk_status"]="ROBUST_SHADOW";accepted.append(x)
        elif r.get("holdout_pass") and n>=3 and agg>0:
            x["risk_status"]="WATCH";watch.append(x)
        else:
            x["risk_status"]="REJECT";rejected.append(x)

    accepted.sort(key=lambda r:r["risk"]["risk_adjusted_score"],reverse=True)
    watch.sort(key=lambda r:r["risk"]["risk_adjusted_score"],reverse=True)
    report={
      "rules":{
        "min_holdout_n":MIN_HOLDOUT_N,"min_positive_pct":MIN_POSITIVE_PCT,
        "min_current_trades":MIN_CURRENT_TRADES,"min_median_rub":MIN_MEDIAN_RUB,
        "max_loss_to_median":MAX_LOSS_TO_MEDIAN
      },
      "robust_count":len(accepted),"watch_count":len(watch),
      "robust_routes":accepted[:MAX_ROUTES],
      "watch_routes":watch[:30]
    }
    (OUT/"moex_calendar_risk_shortlist.json").write_text(
      json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"robust_count":len(accepted),"top":[{
      "spread":r["spread"],"score":r["risk"]["risk_adjusted_score"],
      "holdout":r["holdout"],"current_trades":r["current_trades"]
    } for r in accepted[:MAX_ROUTES]]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
