from __future__ import annotations

import csv, json, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"shadow_state"
EVENT_STATE=ROOT/"event_state"
OUT=ROOT/"promotion_status.json"
REGISTRY=ROOT/"strategy_registry.json"

MOEX_STRATEGY_MAP={
    "CNY":"MOEX_CNY_PERP_CURVE",
    "USD":"MOEX_USD_PERP_CURVE",
    "EUR":"MOEX_EUR_PERP_CURVE",
    "IMOEX":"MOEX_IMOEX_PERP_CURVE",
    "RGBI":"MOEX_RGBI_PERP_CURVE",
}

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


EVENT_MIN_CLOSED=30
EVENT_MIN_DAYS=3
EVENT_MIN_POSITIVE_RATE=0.55
EVENT_MAX_SINGLE_WIN_SHARE=0.50

def event_ledger_gate(path,net_field,units):
    rows=read_csv(path)
    closes=[r for r in rows if r.get("event")=="CLOSE"]
    vals=[]
    dates=set()
    for r in closes:
        try:
            x=float(r.get(net_field) or 0)
        except Exception:
            continue
        vals.append(x)
        d=(r.get("utc") or "")[:10]
        if d:dates.add(d)
    if not vals:
        return {
          "status":"SHADOW","closed_cycles":0,"calendar_days":0,
          "reason":"No closed event-shadow cycles yet",
          "units":units
        }
    total=sum(vals)
    med=statistics.median(vals)
    pos=sum(x>0 for x in vals)/len(vals)
    positive_vals=[x for x in vals if x>0]
    max_win=max(positive_vals) if positive_vals else 0.0
    positive_sum=sum(positive_vals)
    single_win_share=(max_win/positive_sum) if positive_sum>0 else 1.0
    eligible=(
      len(vals)>=EVENT_MIN_CLOSED
      and len(dates)>=EVENT_MIN_DAYS
      and total>0 and med>0
      and pos>=EVENT_MIN_POSITIVE_RATE
      and single_win_share<=EVENT_MAX_SINGLE_WIN_SHARE
    )
    return {
      "status":"PAPER_ELIGIBLE" if eligible else "SHADOW",
      "closed_cycles":len(vals),"calendar_days":len(dates),
      "aggregate_net":total,"median_net":med,
      "positive_cycles_pct":100*pos,
      "worst_cycle":min(vals),"best_cycle":max(vals),
      "largest_win_share_of_positive_pnl":single_win_share,
      "units":units,
      "requirements":{
        "closed_cycles_gte":EVENT_MIN_CLOSED,
        "calendar_days_gte":EVENT_MIN_DAYS,
        "aggregate_net_gt":0,
        "median_net_gt":0,
        "positive_cycles_pct_gte":100*EVENT_MIN_POSITIVE_RATE,
        "largest_win_share_lte":EVENT_MAX_SINGLE_WIN_SHARE
      }
    }

def event_gates():
    return {
      "crypto_perp":event_ledger_gate(
          EVENT_STATE/"crypto_event_ledger.csv","realized_net_bps","bps"),
      "crypto_spot":event_ledger_gate(
          EVENT_STATE/"crypto_spot_event_ledger.csv","realized_net_bps","bps"),
      "crypto_validated_fast":event_ledger_gate(
          EVENT_STATE/"crypto_validated_fast_ledger.csv","realized_net_bps","bps"),
      "crypto_op_gate_okx_taker_v1":event_ledger_gate(
          EVENT_STATE/"op_gate_okx_ledger.csv","realized_net_bps","bps"),
      "crypto_op_gate_okx_maker_v2":event_ledger_gate(
          EVENT_STATE/"op_gate_okx_maker_v2_ledger.csv","realized_net_bps","bps"),
      "crypto_maker_universe":event_ledger_gate(
          EVENT_STATE/"crypto_maker_universe_ledger.csv","realized_net_bps","bps"),
      "moex_universe_v1_deprecated":event_ledger_gate(
          EVENT_STATE/"moex_event_ledger.csv","realized_net_rub","RUB"),
      "moex_universe_v2_guarded":event_ledger_gate(
          EVENT_STATE/"moex_event_v2_ledger.csv","realized_net_rub","RUB"),
      "moex_si_synthetic_v1_deprecated":event_ledger_gate(
          EVENT_STATE/"si_calendar_fast_ledger.csv","realized_net_rub","RUB"),
      "moex_si_page_v1_deprecated":event_ledger_gate(
          EVENT_STATE/"si_calendar_page_ledger.csv","realized_net_rub","RUB"),
      "moex_si_effective_v2":event_ledger_gate(
          EVENT_STATE/"si_calendar_effective_v2_ledger.csv","realized_net_rub","RUB"),
      "moex_calendar_page_universe":event_ledger_gate(
          EVENT_STATE/"moex_calendar_page_universe_ledger.csv","realized_net_rub","RUB")
    }

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
    try:
        reg=json.loads(REGISTRY.read_text(encoding="utf-8"))
        regmap={x.get("id"):x for x in reg.get("strategies",[])}
    except Exception:
        regmap={}
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
        sid=MOEX_STRATEGY_MAP.get(name)
        strat=regmap.get(sid,{})
        research_status=strat.get("status","UNKNOWN")
        if research_status in ("REJECTED_RESEARCH","RESEARCH_SECONDARY"):
            out[name]={
              "status":"RESEARCH_REJECTED" if research_status=="REJECTED_RESEARCH" else "RESEARCH_SECONDARY",
              "research_status":research_status,
              "strong_scans":len(by.get(name,[])),
              "calendar_days":len(dates.get(name,set())),
              "reason":strat.get("reason","Not approved for PAPER promotion")
            }
            continue
        strong=by.get(name,[])
        try:
            returns=[float(r.get("screen_return_on_im_pct") or 0) for r in strong]
            ratios=[float(r.get("swaprate_ratio") or 0) for r in strong if r.get("swaprate_ratio") not in ("",None)]
        except:
            returns=[];ratios=[]
        rule=strat.get("research_rule") or {}
        min_ratio=float(rule.get("min_swaprate_to_breakeven_ratio",0.0) or 0.0)
        min_ret=float(rule.get("min_screen_return_on_im_pct",0.0) or 0.0)
        eligible_rows=[]
        for r in strong:
            try:
                rr=float(r.get("swaprate_ratio") or 0.0)
                rv=float(r.get("screen_return_on_im_pct") or 0.0)
                if rr>=min_ratio and rv>=min_ret:
                    eligible_rows.append(r)
            except Exception:
                pass
        eligible=(
            research_status in ("SHADOW_PRIMARY","SHADOW")
            and len(eligible_rows)>=MOEX_MIN_STRONG_SCANS
            and len(dates.get(name,set()))>=5
            and returns and min(returns)>0
        )
        out[name]={
          "status":"PAPER_ELIGIBLE" if eligible else "SHADOW",
          "research_status":research_status,
          "strong_scans":len(strong),
          "research_rule_eligible_scans":len(eligible_rows),
          "calendar_days":len(dates.get(name,set())),
          "median_screen_return_on_im_pct":statistics.median(returns) if returns else None,
          "min_screen_return_on_im_pct":min(returns) if returns else None,
          "median_swaprate_ratio":statistics.median(ratios) if ratios else None,
          "requirements":{"strong_scans":MOEX_MIN_STRONG_SCANS,"calendar_days":5}
        }
    return out

def main():
    result={"event_driven":event_gates(),
            "legacy_crypto":crypto_gate(),"legacy_moex":moex_gate(),
            "rule":"PAPER_ELIGIBLE is evidence status only. It never enables MICRO_LIVE or LIVE orders."}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
