from __future__ import annotations

import json, math, statistics, time
from collections import deque
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)

BASE="OP"
DAYS=30
LOOKBACK_H=72
ENTRY_ZS=[2.5,3.0,3.5]
MAKER_OFFSETS_BPS=[0.0,1.0,2.0]
THROUGH_BPS=[0.0,1.0]
MAX_MAKER_WAIT_MIN=15
MAX_HOLD_MIN=12*60
EXIT_Z=0.5
STOP_Z=5.0
TRAIN_FRAC=0.70

GATE_MAKER_BPS=2.0
GATE_TAKER_BPS=5.0
OKX_TAKER_BPS=5.0
EXTRA_BUFFER_BPS=4.0
TOTAL_COST_BPS=GATE_MAKER_BPS+OKX_TAKER_BPS+GATE_TAKER_BPS+OKX_TAKER_BPS+EXTRA_BUFFER_BPS

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
        and m.get("active",True) and m.get("base")==BASE and m.get("quote")=="USDT"]
    if not ms:raise RuntimeError(f"{exid}: OP swap unavailable")
    return ex,ms[0]["symbol"]

def fetch_all(ex,sym,tf,since,limit=1000,max_calls=100):
    out=[];cursor=since
    step=60000 if tf=="1m" else 3600000
    for _ in range(max_calls):
        try:rr=ex.fetch_ohlcv(sym,tf,cursor,limit)
        except Exception:break
        if not rr:break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor:break
        cursor=mx+step
        if cursor>=int(time.time()*1000)-step:break
        time.sleep(max(0.02,(getattr(ex,"rateLimit",0) or 0)/1000))
    d={}
    for x in out:
        if len(x)<6:continue
        d[int(x[0])]={"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),
                      "close":float(x[4]),"volume":float(x[5] or 0)}
    return d

def hourly_signals(Ah,Bh,entry_z):
    ts=[t for t in sorted(set(Ah)&set(Bh)) if Ah[t]["volume"]>0 and Bh[t]["volume"]>0]
    hist=deque(maxlen=LOOKBACK_H);signals=[]
    for t in ts:
        x=(Bh[t]["close"]/Ah[t]["close"]-1)*10000
        if len(hist)<LOOKBACK_H:
            hist.append(x);continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist)
        z=(x-mean)/sd if sd>1e-12 else 0
        direction=None
        if z>=entry_z:direction="LONG_GATE_SHORT_OKX"
        elif z<=-entry_z:direction="SHORT_GATE_LONG_OKX"
        if direction:
            signals.append({"bar_ts":t,"ready_ts":t+3600000,"z":z,"mean":mean,"sd":sd,
                            "direction":direction,"gate_close":Ah[t]["close"],"okx_close":Bh[t]["close"]})
        hist.append(x)
    return signals

def minute_key(ts):
    return (ts//60000)*60000

def find_fill(signal,Am,offset,through):
    start=minute_key(signal["ready_ts"])
    if signal["direction"]=="LONG_GATE_SHORT_OKX":
        maker=signal["gate_close"]*(1-offset/10000)
        trigger=maker*(1-through/10000)
        for k in range(MAX_MAKER_WAIT_MIN):
            t=start+k*60000;b=Am.get(t)
            if b and b["volume"]>0 and b["low"]<=trigger:return t,maker,k
    else:
        maker=signal["gate_close"]*(1+offset/10000)
        trigger=maker*(1+through/10000)
        for k in range(MAX_MAKER_WAIT_MIN):
            t=start+k*60000;b=Am.get(t)
            if b and b["volume"]>0 and b["high"]>=trigger:return t,maker,k
    return None,None,None

def execute(signal,Am,Bm,offset,through):
    ft,maker,wait=find_fill(signal,Am,offset,through)
    if ft is None:return None
    hb=Bm.get(ft)
    if not hb:return None
    # Conservative immediate hedge using adverse extreme of the fill minute.
    if signal["direction"]=="LONG_GATE_SHORT_OKX":
        hedge=hb["low"]
    else:
        hedge=hb["high"]

    last=ft+MAX_HOLD_MIN*60000
    for t in range(ft+60000,last+60000,60000):
        ga=Am.get(t);hb=Bm.get(t)
        if not ga or not hb:continue
        amid=ga["close"];bmid=hb["close"]
        basis=(bmid/amid-1)*10000
        z=(basis-signal["mean"])/signal["sd"] if signal["sd"]>1e-12 else 0
        reason=None
        if abs(z)<=EXIT_Z:reason="CONVERGENCE"
        elif abs(z)>=STOP_Z:reason="Z_STOP"
        elif t>=last:reason="MAX_HOLD"
        if not reason:continue

        if signal["direction"]=="LONG_GATE_SHORT_OKX":
            gate_exit=ga["low"];okx_exit=hb["high"]
            raw=((gate_exit/maker-1)+(1-okx_exit/hedge))*10000
        else:
            gate_exit=ga["high"];okx_exit=hb["low"]
            raw=((1-gate_exit/maker)+(okx_exit/hedge-1))*10000
        return {
          "signal_ts":signal["bar_ts"],"ready_ts":signal["ready_ts"],"fill_ts":ft,"exit_ts":t,
          "direction":signal["direction"],"entry_z":signal["z"],"baseline_mean":signal["mean"],
          "maker_offset_bps":offset,"through_bps":through,"maker_price":maker,
          "hedge_price":hedge,"gate_exit":gate_exit,"okx_exit":okx_exit,
          "maker_wait_min":wait,"hold_min":(t-ft)/60000,
          "raw_bps":raw,"cost_bps":TOTAL_COST_BPS,"net_bps":raw-TOTAL_COST_BPS,
          "reason":reason
        }
    return None

def metrics(xs):
    if not xs:return None
    v=[x["net_bps"] for x in xs]
    return {"n":len(v),"positive_pct":100*sum(x>0 for x in v)/len(v),
            "median_net_bps":statistics.median(v),"mean_net_bps":statistics.fmean(v),
            "aggregate_net_bps":sum(v),"worst_net_bps":min(v),"best_net_bps":max(v),
            "median_maker_wait_min":statistics.median(x["maker_wait_min"] for x in xs),
            "median_hold_min":statistics.median(x["hold_min"] for x in xs)}

def simulate(signals,Am,Bm,offset,through):
    trades=[];last_exit=-1;fills=0
    for s in signals:
        if s["ready_ts"]<=last_exit:continue
        x=execute(s,Am,Bm,offset,through)
        if x:
            fills+=1;trades.append(x);last_exit=x["exit_ts"]
    return trades,fills

def main():
    since=int((time.time()-DAYS*86400)*1000)
    gate,gs=build("gate");okx,os=build("okx")
    Ah=fetch_all(gate,gs,"1h",since,500,10)
    Bh=fetch_all(okx,os,"1h",since,500,10)
    Am=fetch_all(gate,gs,"1m",since,1000,60)
    Bm=fetch_all(okx,os,"1m",since,1000,60)

    scenarios=[]
    all_selected=[]
    for ez in ENTRY_ZS:
        sig=hourly_signals(Ah,Bh,ez)
        for off in MAKER_OFFSETS_BPS:
            for thr in THROUGH_BPS:
                tr,fills=simulate(sig,Am,Bm,off,thr)
                if not tr:
                    scenarios.append({"entry_z":ez,"maker_offset_bps":off,"through_bps":thr,
                                      "signals":len(sig),"fills":0,"train":None,"holdout":None})
                    continue
                times=sorted(set(x["signal_ts"] for x in tr))
                cutoff=times[max(0,min(len(times)-1,int(len(times)*TRAIN_FRAC)))]
                train=[x for x in tr if x["signal_ts"]<cutoff]
                hold=[x for x in tr if x["signal_ts"]>=cutoff]
                tm,hm=metrics(train),metrics(hold)
                scenarios.append({"entry_z":ez,"maker_offset_bps":off,"through_bps":thr,
                                  "signals":len(sig),"fills":fills,
                                  "fill_pct":100*fills/max(1,len(sig)),
                                  "cutoff_ts":cutoff,"train":tm,"holdout":hm,
                                  "_trades":tr})

    eligible=[]
    for s in scenarios:
        tm=s.get("train")
        if not tm or tm["n"]<3 or tm["aggregate_net_bps"]<=0 or tm["median_net_bps"]<=0:continue
        score=tm["median_net_bps"]*math.sqrt(tm["n"])*(tm["positive_pct"]/100)
        eligible.append((score,s))
    eligible.sort(key=lambda x:x[0],reverse=True)
    selected=eligible[0][1] if eligible else None
    selected_clean=None
    if selected:
        hm=selected.get("holdout")
        selected_clean={k:v for k,v in selected.items() if k!="_trades"}
        selected_clean["holdout_pass"]=bool(hm and hm["n"]>=2 and hm["aggregate_net_bps"]>0
                                             and hm["median_net_bps"]>0 and hm["positive_pct"]>=60)
        all_selected=selected["_trades"]

    clean_scenarios=[{k:v for k,v in s.items() if k!="_trades"} for s in scenarios]
    report={
      "route":"OP Gate maker -> OKX taker hedge; 1-minute fill/hedge/exit model",
      "days":DAYS,"hour_bars":len(Ah),"minute_gate_bars":len(Am),"minute_okx_bars":len(Bm),
      "cost_bps":TOTAL_COST_BPS,
      "execution_model":"hour-close signal; maker order after close; max 15m later-minute fill; same-minute adverse hedge; minute adverse exit",
      "selected":selected_clean,"scenarios":clean_scenarios
    }
    (OUT/"crypto_op_gate_okx_maker_1m_30d.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"crypto_op_gate_okx_maker_1m_selected_trades.json").write_text(json.dumps(all_selected,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
