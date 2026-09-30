from __future__ import annotations

import csv, json, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"event_state"
CFG=ROOT/"event_route_config.json"
OUT=STATE/"crypto_live_execution_audit.json"
TS=STATE/"crypto_validated_fast_timeseries.csv"

MIN_SAMPLES=24

def median(xs):
    if not xs:return None
    return statistics.median(xs)

def main():
    cfg=json.loads(CFG.read_text(encoding="utf-8"))
    groups={}
    if TS.exists():
        with TS.open("r",encoding="utf-8",newline="") as f:
            for r in csv.DictReader(f):
                key=r.get("route_key")
                if not key:continue
                try:
                    eh=float(r.get("expected_high_bps") or "nan")
                    el=float(r.get("expected_low_bps") or "nan")
                    z=float(r.get("z") or 0)
                    spread=float(r.get("exit_spread_bps") or 0)
                    fee=float(r.get("fee_bps") or 0)
                except Exception:
                    continue
                best=max(eh,el)
                g=groups.setdefault(key,{"best":[],"spread":[],"z":[],"fee":[]})
                g["best"].append(best);g["spread"].append(spread);g["z"].append(abs(z));g["fee"].append(fee)

    audit={}
    for section_name in ("crypto_perp","crypto_spot"):
        section=cfg.get(section_name) or {}
        for key,meta in section.items():
            g=groups.get(key,{"best":[],"spread":[],"z":[],"fee":[]})
            xs=g["best"]
            n=len(xs)
            positive=sum(x>0 for x in xs)
            live_status="NO_LIVE_SAMPLE"
            if n>=MIN_SAMPLES:
                live_status="LIVE_EXECUTABLE_SEEN" if positive>0 else "LIVE_SPREAD_BLOCKED_CURRENT"
            elif n>0:
                live_status="LIVE_WARMUP"
            audit[key]={
              "kind":"PERP" if section_name=="crypto_perp" else "SPOT",
              "historical_status":meta.get("status"),
              "samples":n,
              "positive_executable_samples":positive,
              "positive_executable_pct":100*positive/n if n else None,
              "median_best_expected_bps":median(xs),
              "max_best_expected_bps":max(xs) if xs else None,
              "median_exit_spread_bps":median(g["spread"]),
              "median_fee_bps":median(g["fee"]),
              "max_abs_z":max(g["z"]) if g["z"] else None,
              "live_status":live_status
            }
            meta["live_execution"]=audit[key]

    CFG.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding="utf-8")
    OUT.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "routes":len(audit),
      "live_executable_seen":sum(v["live_status"]=="LIVE_EXECUTABLE_SEEN" for v in audit.values()),
      "blocked":sum(v["live_status"]=="LIVE_SPREAD_BLOCKED_CURRENT" for v in audit.values()),
      "top":sorted(audit.items(),key=lambda kv:(kv[1]["max_best_expected_bps"] if kv[1]["max_best_expected_bps"] is not None else -1e9),reverse=True)[:20]
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
