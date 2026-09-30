from __future__ import annotations

import json, math, statistics, time
from collections import deque
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"; OUT.mkdir(parents=True,exist_ok=True)

BASE="OP"
MAKER_ID="gate"
HEDGE_ID="okx"
DAYS=180
LOOKBACK=72
ENTRY_ZS=[2.5,3.0,3.5]
MAKER_OFFSETS_BPS=[0.0,1.0,2.0]
THROUGH_BPS=[0.0,1.0]
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_H=12
TRAIN_FRAC=0.70

# Verified current regular/VIP0 public schedule context:
# Gate maker 2 bps, taker 5 bps; OKX maker 2 bps, taker 5 bps.
GATE_MAKER_BPS=2.0
GATE_TAKER_BPS=5.0
OKX_TAKER_BPS=5.0
EXTRA_BUFFER_BPS=4.0
TOTAL_FEE_AND_BUFFER_BPS=GATE_MAKER_BPS+OKX_TAKER_BPS+GATE_TAKER_BPS+OKX_TAKER_BPS+EXTRA_BUFFER_BPS

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
        and m.get("active",True) and m.get("base")==BASE and m.get("quote")=="USDT"]
    if not ms: raise RuntimeError(f"{exid}: {BASE}/USDT swap unavailable")
    return ex,ms[0]["symbol"]

def history(ex,sym,since):
    out=[];cursor=since
    for _ in range(16):
        try: rr=ex.fetch_ohlcv(sym,"1h",cursor,500)
        except Exception: break
        if not rr: break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor: break
        cursor=mx+3600000
        if cursor>=int(time.time()*1000)-3600000: break
    d={}
    for x in out:
        if len(x)<6: continue
        d[int(x[0])]={"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),
                      "close":float(x[4]),"volume":float(x[5] or 0)}
    return d

def metrics(xs):
    if not xs:return None
    nets=[x["net_bps"] for x in xs]
    return {"n":len(nets),"positive_pct":100*sum(x>0 for x in nets)/len(nets),
            "median_net_bps":statistics.median(nets),"mean_net_bps":statistics.fmean(nets),
            "aggregate_net_bps":sum(nets),"worst_net_bps":min(nets),"best_net_bps":max(nets),
            "fill_wait_hours_median":statistics.median(x["maker_wait_hours"] for x in xs)}

def simulate(A,B,entry_z,offset_bps,through_bps):
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0]
    idx={t:i for i,t in enumerate(ts)}
    hist=deque(maxlen=LOOKBACK)
    trades=[];signals=0;fills=0
    i=0
    while i<len(ts)-2:
        t=ts[i]
        x=(B[t]["close"]/A[t]["close"]-1)*10000
        if len(hist)<LOOKBACK:
            hist.append(x);i+=1;continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist)
        z=(x-mean)/sd if sd>1e-12 else 0.0
        direction=None
        if z>=entry_z: direction="LONG_GATE_SHORT_OKX"
        elif z<=-entry_z: direction="SHORT_GATE_LONG_OKX"
        if not direction:
            hist.append(x);i+=1;continue

        signals+=1
        gate_close=A[t]["close"]
        if direction=="LONG_GATE_SHORT_OKX":
            maker_px=gate_close*(1-offset_bps/10000)
        else:
            maker_px=gate_close*(1+offset_bps/10000)

        # Maker order is posted AFTER bar t closes. Search up to 3 subsequent hours for a
        # trade-through. No same-bar fill is allowed.
        fill_j=None
        for j in range(i+1,min(i+4,len(ts))):
            ga=A[ts[j]]
            if direction=="LONG_GATE_SHORT_OKX":
                trigger=maker_px*(1-through_bps/10000)
                if ga["low"]<=trigger: fill_j=j;break
            else:
                trigger=maker_px*(1+through_bps/10000)
                if ga["high"]>=trigger: fill_j=j;break
        if fill_j is None:
            hist.append(x);i+=1;continue
        fills+=1

        ft=ts[fill_j]
        hb=B[ft]
        # Conservative immediate hedge proxy using the adverse hourly extreme on OKX.
        if direction=="LONG_GATE_SHORT_OKX":
            okx_entry=hb["low"]  # worst short-sale price in that hour
        else:
            okx_entry=hb["high"] # worst buy price in that hour

        # Position monitoring starts after the fill hour.
        exit_j=None;exit_reason=None
        for j in range(fill_j+1,min(fill_j+MAX_HOLD_H+2,len(ts))):
            tt=ts[j]
            basis=(B[tt]["close"]/A[tt]["close"]-1)*10000
            # Rebuild rolling mean/sd from closes strictly before this decision bar.
            prior=[]
            k0=max(0,j-LOOKBACK)
            for k in range(k0,j):
                ta=ts[k]
                prior.append((B[ta]["close"]/A[ta]["close"]-1)*10000)
            if len(prior)<20: continue
            m=statistics.fmean(prior);s=statistics.pstdev(prior)
            zz=(basis-m)/s if s>1e-12 else 0.0
            if abs(zz)<=EXIT_Z:
                exit_j=j;exit_reason="CONVERGENCE";break
            if abs(zz)>=STOP_Z:
                exit_j=j;exit_reason="Z_STOP";break
            if j-fill_j>=MAX_HOLD_H:
                exit_j=j;exit_reason="MAX_HOLD";break
        if exit_j is None:
            hist.append(x);i+=1;continue

        et=ts[exit_j]
        ga=A[et];hb=B[et]
        # Conservative taker exit: adverse extreme in the exit hour.
        if direction=="LONG_GATE_SHORT_OKX":
            gate_exit=ga["low"]   # sell long Gate at adverse low
            okx_exit=hb["high"]   # buy short OKX at adverse high
            raw=((gate_exit/maker_px-1)+(1-okx_exit/okx_entry))*10000
        else:
            gate_exit=ga["high"]  # buy short Gate at adverse high
            okx_exit=hb["low"]    # sell long OKX at adverse low
            raw=((1-gate_exit/maker_px)+(okx_exit/okx_entry-1))*10000

        net=raw-TOTAL_FEE_AND_BUFFER_BPS
        trades.append({
            "signal_ts":t,"fill_ts":ft,"exit_ts":et,"direction":direction,
            "entry_z":z,"maker_offset_bps":offset_bps,"through_bps":through_bps,
            "maker_price":maker_px,"okx_hedge_price":okx_entry,
            "gate_exit":gate_exit,"okx_exit":okx_exit,
            "maker_wait_hours":fill_j-i,"hold_hours":exit_j-fill_j,
            "raw_bps":raw,"cost_bps":TOTAL_FEE_AND_BUFFER_BPS,
            "net_bps":net,"reason":exit_reason
        })
        # Do not overlap trades in this scenario.
        i=max(i+1,exit_j)
        hist.clear()
        for k in range(max(0,i-LOOKBACK),i):
            ta=ts[k]
            hist.append((B[ta]["close"]/A[ta]["close"]-1)*10000)

    return trades,signals,fills,len(ts)

def main():
    since=int((time.time()-DAYS*86400)*1000)
    gate,gs=build(MAKER_ID);okx,os=build(HEDGE_ID)
    A=history(gate,gs,since);B=history(okx,os,since)
    scenarios=[]
    for ez in ENTRY_ZS:
        for off in MAKER_OFFSETS_BPS:
            for thr in THROUGH_BPS:
                tr,signals,fills,bars=simulate(A,B,ez,off,thr)
                if not tr:
                    scenarios.append({"entry_z":ez,"maker_offset_bps":off,"through_bps":thr,
                                      "bars":bars,"signals":signals,"maker_fills":fills,
                                      "maker_fill_pct":100*fills/signals if signals else 0,
                                      "train":None,"holdout":None})
                    continue
                unique=sorted(set(x["signal_ts"] for x in tr))
                cutoff=unique[max(0,min(len(unique)-1,int(len(unique)*TRAIN_FRAC)))]
                train=[x for x in tr if x["signal_ts"]<cutoff]
                hold=[x for x in tr if x["signal_ts"]>=cutoff]
                scenarios.append({
                    "entry_z":ez,"maker_offset_bps":off,"through_bps":thr,
                    "bars":bars,"signals":signals,"maker_fills":fills,
                    "maker_fill_pct":100*fills/signals if signals else 0,
                    "train":metrics(train),"holdout":metrics(hold),
                    "trades":tr
                })

    # Select scenario on train only.
    eligible=[]
    for s in scenarios:
        tm=s.get("train")
        if not tm or tm["n"]<3:continue
        if tm["aggregate_net_bps"]<=0 or tm["median_net_bps"]<=0:continue
        score=tm["median_net_bps"]*math.sqrt(tm["n"])*(tm["positive_pct"]/100)
        eligible.append((score,s))
    eligible.sort(key=lambda x:x[0],reverse=True)
    selected=eligible[0][1] if eligible else None
    if selected:
        hm=selected.get("holdout")
        selected_summary={k:v for k,v in selected.items() if k!="trades"}
        selected_summary["holdout_pass"]=bool(hm and hm["n"]>=2 and hm["aggregate_net_bps"]>0
                                               and hm["median_net_bps"]>0 and hm["positive_pct"]>=60)
    else:
        selected_summary=None

    report={
      "route":"OP Gate maker -> OKX taker hedge; taker/taker exit",
      "days":DAYS,
      "cost_model":{
        "gate_entry_maker_bps":GATE_MAKER_BPS,
        "okx_entry_taker_bps":OKX_TAKER_BPS,
        "gate_exit_taker_bps":GATE_TAKER_BPS,
        "okx_exit_taker_bps":OKX_TAKER_BPS,
        "extra_buffer_bps":EXTRA_BUFFER_BPS,
        "total_bps":TOTAL_FEE_AND_BUFFER_BPS
      },
      "fill_model":"signal at hour close; maker fill only on later-hour trade-through; hedge/exit use adverse hourly extremes",
      "selected":selected_summary,
      "scenarios":[{k:v for k,v in s.items() if k!="trades"} for s in scenarios]
    }
    (OUT/"crypto_op_gate_okx_maker_fill_180d.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if selected:
        (OUT/"crypto_op_gate_okx_maker_fill_trades.json").write_text(
            json.dumps(selected["trades"],ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
