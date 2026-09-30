from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT/"results"
PROD=ROOT/"production"
OUT=PROD/"event_route_config.json"

def transfer_ok(audit,venue,base):
    try:
        cur=audit["venues"][venue][base]["currency"]
    except Exception:
        return False
    if not cur or not cur.get("available"):
        return False
    if cur.get("deposit") is not True or cur.get("withdraw") is not True:
        return False
    nets=cur.get("networks") or []
    return any(n.get("deposit") is True and n.get("withdraw") is True for n in nets)

def main():
    cfg={
      "updated_from_research":True,
      "crypto_perp":{},
      "crypto_spot":{},
      "crypto_spot_rejected_operational":{},
      "moex":{},
      "notes":[
        "Universe remains fully scanned.",
        "Only holdout-passing routes are eligible for validated shadow entries.",
        "This file never enables real-money trading."
      ]
    }

    audit_path=RESULTS/"crypto_spot_identity_audit.json"
    try:
        identity=json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    except Exception:
        identity={}

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
                va,vb=sorted([r["venue_a"],r["venue_b"]])
                key=f"{r['base']}|{va}|{vb}"
                if dest=="crypto_spot":
                    operational=transfer_ok(identity,r["venue_a"],r["base"]) and transfer_ok(identity,r["venue_b"],r["base"])
                    if not operational:
                        cfg["crypto_spot_rejected_operational"][key]={
                          "base":r["base"],"venue_a":r["venue_a"],"venue_b":r["venue_b"],
                          "entry_z":float(r["selected_entry_z"]),"holdout":h,
                          "status":"REJECTED_TRANSFER_OR_IDENTITY_GATE"
                        }
                        continue
                stress10_pass=(
                    float(h.get("median_net_bps") or 0)>10.0
                    and float(h.get("aggregate_net_bps") or 0)>10.0*int(h.get("n") or 0)
                )
                cfg[dest][key]={
                  "base":r["base"],"venue_a":r["venue_a"],"venue_b":r["venue_b"],
                  "entry_z":float(r["selected_entry_z"]),
                  "train_score":r.get("train_score"),
                  "holdout":h,
                  "stress10_pass":stress10_pass,
                  "stress10_median_proxy_bps":float(h.get("median_net_bps") or 0)-10.0,
                  "stress10_aggregate_proxy_bps":float(h.get("aggregate_net_bps") or 0)-10.0*int(h.get("n") or 0),
                  "status":"VALIDATED_STRESS10" if stress10_pass else "VALIDATED_SHADOW"
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
      "crypto_spot_rejected_operational":len(cfg["crypto_spot_rejected_operational"]),
      "moex":len(cfg["moex"])
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
