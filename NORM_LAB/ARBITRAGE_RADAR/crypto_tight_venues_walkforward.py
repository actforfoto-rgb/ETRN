from __future__ import annotations

import json, math, statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
SRC=OUT/"crypto_tight_venues_60d_trades.json"

MIN_TRAIN=4
MIN_HOLDOUT=2

def metrics(xs):
    if not xs:return None
    vals=[float(x["net_bps"]) for x in xs]
    return {
      "n":len(vals),
      "positive_pct":100*sum(x>0 for x in vals)/len(vals),
      "median_net_bps":statistics.median(vals),
      "mean_net_bps":statistics.fmean(vals),
      "aggregate_net_bps":sum(vals),
      "worst_net_bps":min(vals),
      "best_net_bps":max(vals)
    }

def main():
    rows=json.loads(SRC.read_text(encoding="utf-8"))
    routes=defaultdict(list)
    for r in rows:
        routes[(r["base"],r["venue_a"],r["venue_b"])].append(r)

    selected=[]
    for route,rr in routes.items():
        ts=sorted(set(int(x["entry_ts"]) for x in rr))
        if len(ts)<MIN_TRAIN+MIN_HOLDOUT:continue
        cutoff=ts[int(len(ts)*0.70)]
        choices=[]
        for z in sorted(set(float(x["entry_z_threshold"]) for x in rr)):
            train=[x for x in rr if float(x["entry_z_threshold"])==z and int(x["entry_ts"])<cutoff]
            tm=metrics(train)
            if not tm or tm["n"]<MIN_TRAIN:continue
            if tm["median_net_bps"]<=0 or tm["aggregate_net_bps"]<=0:continue
            score=tm["median_net_bps"]*math.sqrt(tm["n"])*(tm["positive_pct"]/100)
            choices.append((score,z,tm))
        if not choices:continue
        choices.sort(reverse=True,key=lambda x:x[0])
        score,z,tm=choices[0]
        hold=[x for x in rr if float(x["entry_z_threshold"])==z and int(x["entry_ts"])>=cutoff]
        hm=metrics(hold)
        hold_pass=bool(hm and hm["n"]>=MIN_HOLDOUT and hm["positive_pct"]>=66.666
                       and hm["median_net_bps"]>0 and hm["aggregate_net_bps"]>0)
        stress5=bool(hm and hm["median_net_bps"]>5
                     and hm["aggregate_net_bps"]>5*hm["n"])
        selected.append({
          "base":route[0],"venue_a":route[1],"venue_b":route[2],
          "selected_entry_z":z,"train_score":score,"cutoff_ts":cutoff,
          "unique_events":len(ts),"train":tm,"holdout":hm,
          "holdout_pass":hold_pass,"stress5_pass":stress5,
          "source":"60d_non_htx_active_candles"
        })

    selected.sort(key=lambda r:(1 if r["holdout_pass"] else 0,
                                  (r["holdout"] or {}).get("aggregate_net_bps",-1e9),
                                  r["train_score"]),reverse=True)
    result={
      "method":"Per-route chronological 70/30 split; z threshold selected on train only. Non-HTX venues. Both venue candles require positive volume.",
      "routes":selected
    }
    (OUT/"crypto_tight_venues_60d_walkforward.json").write_text(
      json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "routes":len(selected),
      "holdout_pass":sum(r["holdout_pass"] for r in selected),
      "top":[r for r in selected if r["holdout_pass"]][:20]
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
