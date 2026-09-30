from __future__ import annotations

import json, statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
SRC=OUT/"moex_intraday_event_trades.json"

MIN_TRAIN_TRADES=5
TRAIN_POSITIVE_MIN=60.0

def ts(s):
    return datetime.strptime(s,"%Y-%m-%d %H:%M:%S").timestamp()

def metrics(xs):
    if not xs:return None
    nets=[float(x["net_rub"]) for x in xs]
    return {
      "n":len(nets),
      "positive_pct":100*sum(x>0 for x in nets)/len(nets),
      "median_net_rub":statistics.median(nets),
      "mean_net_rub":statistics.fmean(nets),
      "aggregate_net_rub":sum(nets),
      "worst_net_rub":min(nets),
      "best_net_rub":max(nets)
    }

def main():
    rows=json.loads(SRC.read_text(encoding="utf-8"))
    all_ts=sorted(set(ts(r["entry"]) for r in rows))
    cutoff=all_ts[int(len(all_ts)*0.70)]

    by=defaultdict(list)
    for r in rows:by[r["key"]].append(r)

    report=[]
    eligible=[]
    for key,xs in by.items():
        train=[r for r in xs if ts(r["entry"])<cutoff]
        hold=[r for r in xs if ts(r["entry"])>=cutoff]
        tm=metrics(train);hm=metrics(hold)
        selected=bool(tm and tm["n"]>=MIN_TRAIN_TRADES
                      and tm["positive_pct"]>=TRAIN_POSITIVE_MIN
                      and tm["median_net_rub"]>0
                      and tm["aggregate_net_rub"]>0)
        passed=bool(selected and hm and hm["n"]>=2
                    and hm["positive_pct"]>=50
                    and hm["median_net_rub"]>0
                    and hm["aggregate_net_rub"]>0)
        row={"key":key,"type":xs[0].get("type"),"selected_on_train":selected,
             "holdout_pass":passed,"train":tm,"holdout":hm}
        report.append(row)
        if selected:eligible.append(row)

    # ranking comes from TRAIN only
    eligible.sort(key=lambda r:(
       r["train"]["median_net_rub"],
       r["train"]["positive_pct"],
       r["train"]["aggregate_net_rub"]
    ),reverse=True)
    report.sort(key=lambda r:(
       1 if r["selected_on_train"] else 0,
       (r["train"] or {}).get("median_net_rub",-1e99)
    ),reverse=True)

    result={
      "cutoff_epoch":cutoff,
      "cutoff":datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M:%S"),
      "routes":report,
      "train_selected":len(eligible),
      "holdout_passed":sum(r["holdout_pass"] for r in eligible),
      "top_train_selected":eligible[:20]
    }
    (OUT/"moex_intraday_event_walkforward.json").write_text(
      json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "cutoff":result["cutoff"],
      "train_selected":result["train_selected"],
      "holdout_passed":result["holdout_passed"],
      "top":result["top_train_selected"][:10]
    },ensure_ascii=False,indent=2))

if __name__=="__main__":main()
