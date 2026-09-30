from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
SRC=OUT/"crypto_tight_venues_60d_trades.json"

# Current regular/VIP0 assumptions for research sensitivity.
# Gate crypto USDT perpetual: maker 2 bps, taker 5 bps (current published schedule context).
# OKX regular perpetual: maker 2 bps, taker 5 bps.
GATE_MAKER=2.0
GATE_TAKER=5.0
OKX_MAKER=2.0
OKX_TAKER=5.0
EXEC_BUFFER=8.0

def main():
    rows=json.loads(SRC.read_text(encoding="utf-8"))
    xs=[r for r in rows if r.get("base")=="OP" and
        {r.get("venue_a"),r.get("venue_b")}=={"GATE","OKX"} and
        float(r.get("entry_z_threshold") or 0)==3.0]
    # Stored net used 28 bps total model = 20 bps four taker trades + 8 execution buffer.
    scenarios={
      "TAKER_TAKER":{"fee_bps":2*(GATE_TAKER+OKX_TAKER),"fill_model":"immediate both legs"},
      "GATE_MAKER_OKX_TAKER":{"fee_bps":2*(GATE_MAKER+OKX_TAKER),"fill_model":"Gate rests; OKX hedges after fill"},
      "GATE_TAKER_OKX_MAKER":{"fee_bps":2*(GATE_TAKER+OKX_MAKER),"fill_model":"OKX rests; Gate hedges after fill"},
      "MAKER_MAKER":{"fee_bps":2*(GATE_MAKER+OKX_MAKER),"fill_model":"both rest; highest adverse-selection/fill risk"}
    }
    out=[]
    old_total=28.0
    for name,s in scenarios.items():
        total=s["fee_bps"]+EXEC_BUFFER
        nets=[float(r["net_bps"])+(old_total-total) for r in xs]
        if nets:
            nets2=sorted(nets)
            n=len(nets)
            out.append({
              "scenario":name,"n":n,"modeled_fee_bps":s["fee_bps"],
              "execution_buffer_bps":EXEC_BUFFER,"modeled_total_cost_bps":total,
              "positive_pct":100*sum(x>0 for x in nets)/n,
              "median_net_bps":nets2[n//2],
              "aggregate_net_bps":sum(nets),
              "worst_net_bps":min(nets),"best_net_bps":max(nets),
              "fill_risk_note":s["fill_model"]
            })
    report={
      "route":"OP Gate↔OKX",
      "purpose":"fee sensitivity only; maker fill probability and adverse selection are NOT included",
      "historical_source":"crypto_tight_venues_60d_trades.json",
      "scenarios":out,
      "rule":"Maker scenarios cannot promote to PAPER until live passive-order fill probability and post-fill hedge slippage are measured."
    }
    (OUT/"crypto_op_gate_okx_maker_sensitivity.json").write_text(
      json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
