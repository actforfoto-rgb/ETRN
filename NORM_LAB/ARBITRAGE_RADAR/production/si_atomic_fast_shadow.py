from __future__ import annotations

import csv, json, math, statistics, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"si_atomic_state.json"
LAST_FILE=STATE_DIR/"si_atomic_last.json"
TS_FILE=STATE_DIR/"si_atomic_timeseries.csv"
LEDGER_FILE=STATE_DIR/"si_atomic_ledger.csv"

BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-SI-ATOMIC-SHADOW/1.0"})
SECID="SIZ6SIH7"

HIST_BARS=90
ENTRY_Z=2.5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=1800
SAMPLES=4
SLEEP_SEC=10
EXTRA_BUFFER_RUB=2.0
MIN_EXPECTED_NET_RUB=5.0

TS_FIELDS=["utc","bid","ask","mid","baseline_mean","baseline_sd","z",
           "roundtrip_fee_rub","width_rub","expected_sell_rub","expected_buy_rub","position"]
LEDGER_FIELDS=["utc","event","direction","entry_z","exit_z","entry_price","exit_price",
               "expected_net_rub","realized_net_rub","hold_sec","reason"]

def utc():return datetime.now(timezone.utc).isoformat()

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=15);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def meta():
    j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{SECID}.json",{"iss.meta":"off"})
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s or not m:return None
    a=s[0];b=m[0]
    ms=num(a.get("MINSTEP")) or 1.0
    sp=num(a.get("STEPPRICE")) or ms
    return {
      "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
      "last":num(b.get("LAST")),"trades":num(b.get("NUMTRADES")) or 0,
      "volume":num(b.get("VOLTODAY")) or 0,"oi":num(b.get("OPENPOSITION")) or 0,
      "unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0,
      "scalper_fee":num(a.get("SCALPERFEE")) or 0.0,
      "im":num(a.get("INITIALMARGIN")) or 0.0,
      "minstep":ms,"stepprice":sp,
      "expiry":a.get("LASTTRADEDATE"),"shortname":a.get("SHORTNAME")
    }

def baseline():
    till=datetime.now(timezone.utc)
    frm=till-timedelta(days=3)
    j=get(f"{BASE}/engines/futures/markets/forts/securities/{SECID}/candles.json",
          {"interval":1,"from":frm.strftime("%Y-%m-%d"),"till":till.strftime("%Y-%m-%d"),
           "iss.meta":"off"})
    rr=rows(j,"candles")
    xs=[num(x.get("close")) for x in rr if num(x.get("close")) is not None][-HIST_BARS:]
    if len(xs)<20:return None
    sd=statistics.pstdev(xs)
    if sd<=1e-12:return None
    return {"mean":statistics.fmean(xs),"sd":sd,"bars":len(xs)}

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"position":None}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append_csv(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def once(bl,st):
    m=meta()
    if not m or m["bid"] is None or m["ask"] is None or m["bid"]<=0 or m["ask"]<=0:
        return {"utc":utc(),"error":"no two-sided atomic spread quote"}
    mid=(m["bid"]+m["ask"])/2
    z=(mid-bl["mean"])/bl["sd"]
    width=(m["ask"]-m["bid"])*m["unit"]
    rt_fee=2*m["fee"]

    # High spread -> SELL atomic now, BUY back near mean.
    expected_sell=(m["bid"]-bl["mean"])*m["unit"]-width-rt_fee-EXTRA_BUFFER_RUB
    # Low spread -> BUY atomic now, SELL near mean.
    expected_buy=(bl["mean"]-m["ask"])*m["unit"]-width-rt_fee-EXTRA_BUFFER_RUB

    pos=st.get("position")
    if pos:
        hold=time.time()-pos["opened_ts"]
        if pos["direction"]=="SELL":
            pnl=(pos["entry_price"]-m["ask"])*m["unit"]-rt_fee-EXTRA_BUFFER_RUB
            exit_px=m["ask"]
        else:
            pnl=(m["bid"]-pos["entry_price"])*m["unit"]-rt_fee-EXTRA_BUFFER_RUB
            exit_px=m["bid"]
        reason=None
        if abs(z)<=EXIT_Z:reason="CONVERGENCE"
        elif abs(z)>=STOP_Z:reason="Z_STOP"
        elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
        if reason:
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{
              "utc":utc(),"event":"CLOSE","direction":pos["direction"],
              "entry_z":pos["entry_z"],"exit_z":z,"entry_price":pos["entry_price"],
              "exit_price":exit_px,"expected_net_rub":pos["expected_net_rub"],
              "realized_net_rub":pnl,"hold_sec":hold,"reason":reason})
            st["position"]=None
    else:
        if z>=ENTRY_Z and expected_sell>=MIN_EXPECTED_NET_RUB:
            st["position"]={"direction":"SELL","opened_ts":time.time(),"entry_z":z,
                            "entry_price":m["bid"],"expected_net_rub":expected_sell}
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{
              "utc":utc(),"event":"OPEN","direction":"SELL","entry_z":z,
              "entry_price":m["bid"],"expected_net_rub":expected_sell,
              "reason":"ATOMIC_CALENDAR_HIGH"})
        elif z<=-ENTRY_Z and expected_buy>=MIN_EXPECTED_NET_RUB:
            st["position"]={"direction":"BUY","opened_ts":time.time(),"entry_z":z,
                            "entry_price":m["ask"],"expected_net_rub":expected_buy}
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{
              "utc":utc(),"event":"OPEN","direction":"BUY","entry_z":z,
              "entry_price":m["ask"],"expected_net_rub":expected_buy,
              "reason":"ATOMIC_CALENDAR_LOW"})

    row={"utc":utc(),"bid":m["bid"],"ask":m["ask"],"mid":mid,
         "baseline_mean":bl["mean"],"baseline_sd":bl["sd"],"z":z,
         "roundtrip_fee_rub":rt_fee,"width_rub":width,
         "expected_sell_rub":expected_sell,"expected_buy_rub":expected_buy,
         "position":st.get("position"),"market":m}
    return row

def main():
    bl=baseline()
    if not bl:raise SystemExit("atomic spread has insufficient 1m candle history")
    st=load_state();samples=[]
    for i in range(SAMPLES):
        try:r=once(bl,st)
        except Exception as e:r={"utc":utc(),"error":f"{type(e).__name__}: {e}"}
        samples.append(r)
        if "error" not in r:
            append_csv(TS_FILE,TS_FIELDS,r)
            save_state(st)
        if i<SAMPLES-1:time.sleep(SLEEP_SEC)
    report={"utc":utc(),"secid":SECID,"baseline":bl,"samples":samples,"position":st.get("position")}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"baseline":bl,"last":samples[-1] if samples else None,
                      "position":st.get("position")},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
