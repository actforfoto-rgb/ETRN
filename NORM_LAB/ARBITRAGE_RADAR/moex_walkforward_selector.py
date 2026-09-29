from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
SRC=OUT/"moex_cycle_first_trigger_summary.json"
DEST=OUT/"moex_walkforward_selection.json"

MIN_TRAIN_CYCLES=2
MIN_TRAIN_STRESS_POSITIVE_PCT=66.0

def main():
    if not SRC.exists():
        raise SystemExit("moex_cycle_first_trigger_summary.json missing")
    rows=json.loads(SRC.read_text(encoding="utf-8"))
    families=sorted(set(r["family"] for r in rows))
    result=[]

    for fam in families:
        train=[
            r for r in rows
            if r["family"]==fam and r["split"]=="TRAIN_2025"
            and r["n_cycles"]>=MIN_TRAIN_CYCLES
            and r["stress_median_return_on_im_pct"]>0
            and r["stress_aggregate_net_rub"]>0
            and r["stress_positive_cycles_pct"]>=MIN_TRAIN_STRESS_POSITIVE_PCT
        ]
        if not train:
            result.append({
                "family":fam,"status":"REJECT_TRAIN",
                "reason":"No 2025 threshold passed conservative train criteria"
            })
            continue

        # Parsimony rule: choose the LOWEST qualifying threshold, not the best-looking one.
        train.sort(key=lambda x:x["ratio_threshold"])
        chosen=train[0]
        th=chosen["ratio_threshold"]

        hold=next((
            r for r in rows
            if r["family"]==fam and r["split"]=="HOLDOUT_2026"
            and abs(r["ratio_threshold"]-th)<1e-12
        ),None)

        if not hold:
            result.append({
                "family":fam,"status":"NO_HOLDOUT",
                "chosen_threshold":th,"train":chosen
            })
            continue

        hold_ok=(
            hold["n_cycles"]>=1
            and hold["median_return_on_im_pct"]>0
            and hold["aggregate_net_rub"]>0
            and hold["stress_median_return_on_im_pct"]>0
            and hold["stress_aggregate_net_rub"]>0
        )
        strong=(
            hold_ok and hold["n_cycles"]>=2
            and hold["positive_cycles_pct"]>=66.0
            and hold["stress_positive_cycles_pct"]>=66.0
        )
        status="HOLDOUT_STRONG" if strong else ("HOLDOUT_PASS" if hold_ok else "HOLDOUT_FAIL")
        result.append({
            "family":fam,"status":status,
            "chosen_threshold":th,
            "selection_rule":"lowest 2025 threshold with >=2 cycles, stress median/aggregate >0 and stress positive >=66%",
            "train":chosen,"holdout":hold
        })

    DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
