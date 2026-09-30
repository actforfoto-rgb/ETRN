from __future__ import annotations

import json, statistics
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
TRADES=OUT/"moex_calendar_event_selected_trades.json"
SHORTLIST=OUT/"moex_calendar_risk_shortlist.json"
CAPITAL=OUT/"moex_calendar_capital_economics.json"

def dt(s):return datetime.fromisoformat(s)

def metrics(xs):
    if not xs:return None
    vals=[float(x["net_rub"]) for x in xs]
    starts=[dt(x["entry_ts"]) for x in xs]
    ends=[dt(x["exit_ts"]) for x in xs]
    span_days=max(1e-9,(max(ends)-min(starts)).total_seconds()/86400)
    return {
      "n":len(vals),
      "positive_pct":100*sum(x>0 for x in vals)/len(vals),
      "median_net_rub":statistics.median(vals),
      "mean_net_rub":statistics.fmean(vals),
      "aggregate_net_rub":sum(vals),
      "worst_net_rub":min(vals),"best_net_rub":max(vals),
      "span_days":span_days,
      "events_per_7d":len(vals)/span_days*7
    }

def simulate(events,slots):
    # Decisions use only train_score. No holdout result enters selection.
    by_entry={}
    for e in events:
        by_entry.setdefault(e["entry_ts"],[]).append(e)
    active=[]
    accepted=[]
    skipped_overlap=0
    for ts in sorted(by_entry,key=dt):
        t=dt(ts)
        active=[x for x in active if dt(x["exit_ts"])>t]
        free=slots-len(active)
        if free<=0:
            skipped_overlap+=len(by_entry[ts]);continue
        choices=sorted(by_entry[ts],key=lambda x:float(x.get("train_score") or 0),reverse=True)
        for e in choices[:free]:
            active.append(e);accepted.append(e)
        skipped_overlap+=max(0,len(choices)-free)
    return accepted,skipped_overlap

def main():
    rows=json.loads(TRADES.read_text(encoding="utf-8"))
    robust=json.loads(SHORTLIST.read_text(encoding="utf-8"))
    robust_set={r["spread"] for r in robust.get("robust_routes",[])}
    events=[r for r in rows if r.get("phase")=="HOLDOUT" and r.get("holdout_pass")
            and r.get("spread") in robust_set]

    capmap={}
    if CAPITAL.exists():
        try:
            ce=json.loads(CAPITAL.read_text(encoding="utf-8"))
            capmap={r["spread"]:float(r.get("conservative_sum_leg_im_rub") or 0)
                    for r in ce.get("routes",[]) if float(r.get("conservative_sum_leg_im_rub") or 0)>0}
        except:pass

    out=[]
    for slots in (1,2,3):
        accepted,skipped=simulate(events,slots)
        m=metrics(accepted)
        if m:
            # Conservative capital reserve: for N slots reserve N times the largest
            # current sum-leg IM among routes actually used.
            used={e["spread"] for e in accepted}
            max_im=max([capmap.get(x,0) for x in used] or [0])
            reserve=max_im*slots
            m.update({
              "slots":slots,"skipped_overlap_events":skipped,
              "routes_used":sorted(used),
              "reserved_capital_proxy_rub":reserve,
              "aggregate_return_on_reserved_capital_pct":(
                  m["aggregate_net_rub"]/reserve*100 if reserve>0 else None)
            })
        out.append(m)

    by_route={}
    for e in events:
        by_route.setdefault(e["spread"],[]).append(e)
    route_metrics=[{"spread":k,**metrics(v)} for k,v in by_route.items()]
    route_metrics.sort(key=lambda x:x["aggregate_net_rub"],reverse=True)

    report={
      "selection_rule":"holdout events from robust routes; simultaneous entries selected by train_score only",
      "eligible_holdout_events":len(events),
      "robust_routes":sorted(robust_set),
      "slot_portfolios":out,
      "route_holdout_metrics":route_metrics
    }
    (OUT/"moex_calendar_portfolio_holdout.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
