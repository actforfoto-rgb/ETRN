from __future__ import annotations

import json, math, statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"

SOURCES=[
 ("PERP", OUT/"crypto_dislocation_trades.json"),
 ("SPOT", OUT/"crypto_spot_inventory_trades.json"),
]

MIN_TRAIN=6
TRAIN_POSITIVE_MIN=0.65
MAX_TRAIN_WORST_BPS=-80.0

def metrics(xs):
    if not xs:return None
    nets=[float(x["net_bps"]) for x in xs]
    return {
      "n":len(nets),
      "positive_pct":100*sum(x>0 for x in nets)/len(nets),
      "median_net_bps":statistics.median(nets),
      "mean_net_bps":statistics.fmean(nets),
      "aggregate_net_bps":sum(nets),
      "worst_net_bps":min(nets),
      "best_net_bps":max(nets)
    }

def select_source(kind,path):
    rows=json.loads(path.read_text(encoding="utf-8"))
    ts=sorted(set(int(r["entry_ts"]) for r in rows))
    if len(ts)<10:return {"kind":kind,"error":"insufficient timestamps"}
    cutoff=ts[int(len(ts)*0.70)]
    train=[r for r in rows if int(r["entry_ts"])<cutoff]
    hold=[r for r in rows if int(r["entry_ts"])>=cutoff]

    groups=defaultdict(list)
    for r in train:
        key=(r["base"],r["venue_a"],r["venue_b"],float(r["entry_z_threshold"]))
        groups[key].append(r)

    route_candidates=defaultdict(list)
    for key,xs in groups.items():
        m=metrics(xs)
        if not m or m["n"]<MIN_TRAIN:continue
        if m["positive_pct"]<TRAIN_POSITIVE_MIN*100:continue
        if m["median_net_bps"]<=0 or m["aggregate_net_bps"]<=0:continue
        if m["worst_net_bps"]<MAX_TRAIN_WORST_BPS:continue
        # train-only score: reward median, breadth, and hit rate; modest sample bonus.
        score=m["median_net_bps"]*math.sqrt(m["n"])*(m["positive_pct"]/100)
        route=(key[0],key[1],key[2])
        route_candidates[route].append((score,key,m))

    selected=[]
    for route,cands in route_candidates.items():
        cands.sort(key=lambda x:x[0],reverse=True)
        score,key,tm=cands[0]
        hx=[r for r in hold if (r["base"],r["venue_a"],r["venue_b"],float(r["entry_z_threshold"]))==key]
        hm=metrics(hx)
        selected.append({
          "kind":kind,"base":key[0],"venue_a":key[1],"venue_b":key[2],
          "selected_entry_z":key[3],"train_score":score,"train":tm,
          "holdout":hm,"cutoff_ts":cutoff,
          "holdout_pass":bool(hm and hm["n"]>=2 and hm["positive_pct"]>=50
                              and hm["median_net_bps"]>0 and hm["aggregate_net_bps"]>0)
        })
    selected.sort(key=lambda r:(
        1 if r["holdout_pass"] else 0,
        (r["holdout"] or {}).get("aggregate_net_bps",-1e99),
        r["train_score"]
    ),reverse=True)

    # De-duplicated holdout portfolio: at each base+entry timestamp take only the
    # selected route with highest TRAIN score; never pick based on holdout P&L.
    chosen_keys={(r["base"],r["venue_a"],r["venue_b"],r["selected_entry_z"]):r for r in selected}
    candidates=[]
    for r in hold:
        k=(r["base"],r["venue_a"],r["venue_b"],float(r["entry_z_threshold"]))
        sel=chosen_keys.get(k)
        if sel:
            candidates.append({**r,"train_score":sel["train_score"]})
    bucket=defaultdict(list)
    for r in candidates:
        bucket[(int(r["entry_ts"]),r["base"])].append(r)
    portfolio=[]
    for _,xs in sorted(bucket.items()):
        xs.sort(key=lambda x:x["train_score"],reverse=True)
        portfolio.append(xs[0])
    pm=metrics(portfolio)

    return {
      "kind":kind,"cutoff_ts":cutoff,"train_unique_hours":len(set(int(r["entry_ts"]) for r in train)),
      "holdout_unique_hours":len(set(int(r["entry_ts"]) for r in hold)),
      "selected_routes":selected,
      "holdout_portfolio_de_duplicated":pm,
      "holdout_portfolio_trades":portfolio
    }

def main():
    out=[]
    for kind,path in SOURCES:
        if not path.exists():
            out.append({"kind":kind,"error":"source missing"})
            continue
        out.append(select_source(kind,path))
    (OUT/"crypto_event_walkforward.json").write_text(
      json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    brief=[]
    for x in out:
        if "error" in x:
            brief.append(x);continue
        brief.append({
          "kind":x["kind"],
          "selected_routes":len(x["selected_routes"]),
          "holdout_pass_routes":sum(r["holdout_pass"] for r in x["selected_routes"]),
          "top_routes":x["selected_routes"][:10],
          "portfolio":x["holdout_portfolio_de_duplicated"]
        })
    print(json.dumps(brief,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
