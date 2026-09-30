from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT/"results"
PROD=ROOT/"production"
OUT=PROD/"event_route_config.json"

def main():
    cfg={
      "updated_from_research":True,
      "crypto_perp":{},
      "crypto_spot":{},
      "moex":{},
      "notes":[
        "Universe remains fully scanned.",
        "Only holdout-passing routes are eligible for validated shadow entries.",
        "This file never enables real-money trading."
      ]
    }

    cp=RESULTS/"crypto_event_walkforward.json"
    if cp.exists():
        arr=json.loads(cp.read_text(encoding="utf-8"))
        for block in arr:
            kind=block.get("kind")
            dest="crypto_perp" if kind=="PERP" else "crypto_spot" if kind=="SPOT" else None
            if not dest:continue
            for r in block.get("selected_routes",[]):
                if not r.get("holdout_pass"):continue
                h=r.get("holdout") or {}
                if h.get("n",0)<2 or h.get("aggregate_net_bps",0)<=0:continue
                key=f"{r['base']}|{r['venue_a']}|{r['venue_b']}"
                cfg[dest][key]={
                  "base":r["base"],"venue_a":r["venue_a"],"venue_b":r["venue_b"],
                  "entry_z":float(r["selected_entry_z"]),
                  "train_score":r.get("train_score"),
                  "holdout":h,
                  "status":"VALIDATED_SHADOW"
                }

    mp=RESULTS/"moex_intraday_event_walkforward.json"
    if mp.exists():
        m=json.loads(mp.read_text(encoding="utf-8"))
        for r in m.get("routes",[]):
            if not r.get("holdout_pass"):continue
            h=r.get("holdout") or {}
            if h.get("n",0)<2 or h.get("aggregate_net_rub",0)<=0:continue
            cfg["moex"][r["key"]]={
              "key":r["key"],"type":r.get("type"),
              "train":r.get("train"),"holdout":h,
              "status":"VALIDATED_SHADOW"
            }

    OUT.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "crypto_perp":len(cfg["crypto_perp"]),
      "crypto_spot":len(cfg["crypto_spot"]),
      "moex":len(cfg["moex"])
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
