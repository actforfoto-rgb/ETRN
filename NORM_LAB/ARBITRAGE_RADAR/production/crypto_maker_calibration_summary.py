from __future__ import annotations

import csv, json, statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"event_state"
CSV=STATE/"crypto_maker_fill_calibration.csv"
OUT=STATE/"crypto_maker_fill_calibration_summary.json"
RESEARCH=ROOT.parent/"results"/"crypto_maker_taker_180d.json"

MIN_OBS=50
MIN_FILLS=10
MIN_FILL_RATE=0.10
MAX_MEDIAN_ADVERSE_BPS=10.0
MAX_P90_ADVERSE_BPS=20.0

def read():
    if not CSV.exists():return []
    with CSV.open("r",encoding="utf-8",newline="") as f:return list(csv.DictReader(f))

def q(xs,p):
    if not xs:return None
    s=sorted(xs);i=round((len(s)-1)*p);return s[i]

def main():
    rows=read()
    hist={}
    if RESEARCH.exists():
        try:
            arr=json.loads(RESEARCH.read_text(encoding="utf-8"))
            for r in arr:
                k=f"{r['base']}|{r['maker_venue']}|{r['hedge_venue']}"
                h=r.get("holdout") or {}
                if k not in hist or float(h.get("median_net_bps") or -1e99)>float(hist[k].get("median_net_bps") or -1e99):
                    hist[k]=h
        except:pass

    groups=defaultdict(list)
    for r in rows:groups[r["route"]].append(r)
    out={}
    for route,xs in groups.items():
        fills=[x for x in xs if str(x.get("filled")).lower()=="true"]
        adverse=[]
        for x in fills:
            try:
                raw=float(x.get("hedge_slippage_bps") or 0)
                # BUY maker => hedge is SELL, price rise is favorable.
                # SELL maker => hedge is BUY, price rise is adverse.
                adv=raw if x.get("side")=="SELL" else -raw
                adverse.append(adv)
            except:pass
        n=len(xs);nf=len(fills);fillrate=nf/n if n else 0
        med=statistics.median(adverse) if adverse else None
        p90=q(adverse,.90)
        h=hist.get(route) or {}
        hmed=float(h.get("median_net_bps") or 0) if h else None
        readiness=(n>=MIN_OBS and nf>=MIN_FILLS)
        execution_pass=bool(readiness and fillrate>=MIN_FILL_RATE and med is not None
                            and med<=MAX_MEDIAN_ADVERSE_BPS and p90 is not None
                            and p90<=MAX_P90_ADVERSE_BPS)
        residual=(hmed-med) if hmed is not None and med is not None else None
        status="CALIBRATING"
        if readiness:
            status="EXECUTION_CALIBRATION_PASS" if execution_pass else "EXECUTION_CALIBRATION_FAIL"
        out[route]={
          "status":status,"observations":n,"fills":nf,"fill_rate_pct":100*fillrate,
          "median_adverse_hedge_bps":med,"p90_adverse_hedge_bps":p90,
          "best_adverse_bps":min(adverse) if adverse else None,
          "worst_adverse_bps":max(adverse) if adverse else None,
          "historical_holdout_median_bps":hmed,
          "historical_median_minus_empirical_adverse_bps":residual,
          "requirements":{"observations_gte":MIN_OBS,"fills_gte":MIN_FILLS,
                          "fill_rate_pct_gte":100*MIN_FILL_RATE,
                          "median_adverse_bps_lte":MAX_MEDIAN_ADVERSE_BPS,
                          "p90_adverse_bps_lte":MAX_P90_ADVERSE_BPS}
        }
    report={"utc":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "routes":out,"total_observations":len(rows)}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
