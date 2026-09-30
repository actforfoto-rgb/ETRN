from __future__ import annotations

import csv, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"event_state"
CFG=ROOT/"event_route_config.json"
OUT=ROOT/"execution_gate_status.json"

MIN_TRIGGER_OBS_FAIL=6
MIN_POSITIVE_EXEC_OBS=2

def read_csv(path):
    if not path.exists():return []
    with path.open("r",encoding="utf-8",newline="") as f:
        return list(csv.DictReader(f))

def fnum(r,k,default=None):
    try:return float(r.get(k))
    except:return default

def crypto_validated():
    rows=read_csv(STATE/"crypto_validated_fast_timeseries.csv")
    try:cfg=json.loads(CFG.read_text(encoding="utf-8"))
    except:cfg={}
    req={}
    for key,r in (cfg.get("crypto_perp") or {}).items():
        req[key]=float(r.get("entry_z") or 2.5)
    for key,r in (cfg.get("crypto_spot") or {}).items():
        req[key]=float(r.get("entry_z") or 2.5)

    by={}
    for r in rows:
        key=r.get("route_key")
        if not key:continue
        z=abs(fnum(r,"z",0.0) or 0.0)
        threshold=req.get(key,2.5)
        eh=fnum(r,"expected_high_bps",-1e9)
        el=fnum(r,"expected_low_bps",-1e9)
        best=max(eh,el)
        x=by.setdefault(key,{"samples":0,"trigger_obs":0,"positive_exec_obs":0,
                             "max_expected_net_bps":-1e99,"entry_z":threshold})
        x["samples"]+=1
        x["max_expected_net_bps"]=max(x["max_expected_net_bps"],best)
        if z>=threshold:
            x["trigger_obs"]+=1
            if best>0:x["positive_exec_obs"]+=1
    out={}
    for key,x in by.items():
        if x["positive_exec_obs"]>=MIN_POSITIVE_EXEC_OBS:
            status="EXECUTION_CONFIRMED_SHADOW"
        elif x["trigger_obs"]>=MIN_TRIGGER_OBS_FAIL and x["positive_exec_obs"]==0:
            status="EXECUTION_FAIL"
        else:
            status="WAITING_FOR_EVENT"
        out[key]={**x,"status":status}
    return out

def op_gate_okx():
    rows=read_csv(STATE/"op_gate_okx_timeseries.csv")
    x={"samples":0,"trigger_obs":0,"positive_exec_obs":0,
       "max_expected_net_bps":None,"entry_z":3.0}
    bests=[]
    for r in rows:
        z=abs(fnum(r,"z",0.0) or 0.0)
        best=max(fnum(r,"expected_high_bps",-1e9),fnum(r,"expected_low_bps",-1e9))
        x["samples"]+=1;bests.append(best)
        if z>=3.0:
            x["trigger_obs"]+=1
            if best>0:x["positive_exec_obs"]+=1
    x["max_expected_net_bps"]=max(bests) if bests else None
    if x["positive_exec_obs"]>=MIN_POSITIVE_EXEC_OBS:
        x["status"]="EXECUTION_CONFIRMED_SHADOW"
    elif x["trigger_obs"]>=MIN_TRIGGER_OBS_FAIL and x["positive_exec_obs"]==0:
        x["status"]="EXECUTION_FAIL"
    else:x["status"]="WAITING_FOR_EVENT"
    return x

def tight_crypto_gate():
    rows=read_csv(STATE/"crypto_tight_fast_timeseries.csv")
    by={}
    for r in rows:
        key=r.get("route")
        if not key:continue
        z=abs(fnum(r,"z",0.0) or 0.0)
        best=max(fnum(r,"expected_high_bps",-1e9),fnum(r,"expected_low_bps",-1e9))
        x=by.setdefault(key,{"samples":0,"trigger_obs":0,"positive_exec_obs":0,
                             "max_expected_net_bps":-1e99,"entry_z":3.0})
        x["samples"]+=1
        x["max_expected_net_bps"]=max(x["max_expected_net_bps"],best)
        if z>=3.0:
            x["trigger_obs"]+=1
            if best>0:x["positive_exec_obs"]+=1
    out={}
    for key,x in by.items():
        if x["positive_exec_obs"]>=MIN_POSITIVE_EXEC_OBS:
            status="EXECUTION_CONFIRMED_SHADOW"
        elif x["trigger_obs"]>=MIN_TRIGGER_OBS_FAIL and x["positive_exec_obs"]==0:
            status="EXECUTION_FAIL"
        else:
            status="WAITING_FOR_EVENT"
        out[key]={**x,"status":status}
    return out

def si_gate():
    rows=read_csv(STATE/"si_calendar_fast_timeseries.csv")
    x={"samples":0,"trigger_obs":0,"positive_exec_obs":0,
       "max_expected_net_rub":None,"entry_z":3.0}
    bests=[]
    for r in rows:
        z=abs(fnum(r,"z",0.0) or 0.0)
        best=max(fnum(r,"expected_high_rub",-1e99),fnum(r,"expected_low_rub",-1e99))
        x["samples"]+=1;bests.append(best)
        if z>=3.0:
            x["trigger_obs"]+=1
            if best>0:x["positive_exec_obs"]+=1
    x["max_expected_net_rub"]=max(bests) if bests else None
    if x["positive_exec_obs"]>=MIN_POSITIVE_EXEC_OBS:
        x["status"]="EXECUTION_CONFIRMED_SHADOW"
    elif x["trigger_obs"]>=MIN_TRIGGER_OBS_FAIL and x["positive_exec_obs"]==0:
        x["status"]="EXECUTION_FAIL"
    else:x["status"]="WAITING_FOR_EVENT"

    prows=read_csv(STATE/"si_calendar_page_timeseries.csv")
    atomic_lock=[]
    for r in prows:
        a=fnum(r,"lock_sell_atomic_buy_synth_rub")
        b=fnum(r,"lock_sell_synth_buy_atomic_rub")
        if a is not None:atomic_lock.append(a)
        if b is not None:atomic_lock.append(b)
    x["atomic_page_samples"]=len(prows)
    x["best_atomic_lock_rub"]=max(atomic_lock) if atomic_lock else None
    x["atomic_lock_positive_samples"]=sum(v>0 for v in atomic_lock)
    return x

def main():
    result={
      "crypto_historical_whitelist_execution":crypto_validated(),
      "crypto_op_gate_okx_60d_candidate":op_gate_okx(),
      "crypto_tight_venues":tight_crypto_gate(),
      "moex_si_calendar":si_gate(),
      "rule":"Historical edge alone never promotes. A route must survive live executable bid/ask/VWAP observations."
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
