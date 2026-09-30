from __future__ import annotations

import csv, json, math, re, statistics, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"si_calendar_page_state.json"
TS_FILE=STATE_DIR/"si_calendar_page_timeseries.csv"
LEDGER_FILE=STATE_DIR/"si_calendar_page_ledger.csv"
LAST_FILE=STATE_DIR/"si_calendar_page_last.json"

PAGE="https://www.moex.com/ru/derivatives/spreads/calendar-spreads.aspx"
ISS="https://iss.moex.com/iss"
CODE="Si-12.26-3.27"
NEAR="SiZ6"
FAR="SiH7"

S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-SI-CALENDAR-PAGE/1.0"})

LOOKBACK=24
ENTRY_Z=2.5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_MIN=360
SAMPLES=2
SLEEP_SEC=15
EXTRA_RUB=2.0
MIN_EXPECTED_NET_RUB=5.0

TS_FIELDS=[
 "utc","atomic_bid","atomic_ask","atomic_mid","last","volume_contracts","volume_rub","trades",
 "synthetic_bid","synthetic_ask","synthetic_mid","atomic_minus_synth_mid",
 "lock_sell_atomic_buy_synth_rub","lock_sell_synth_buy_atomic_rub",
 "baseline_mean","baseline_sd","z","expected_meanrev_sell_rub","expected_meanrev_buy_rub",
 "position"
]
LEDGER_FIELDS=[
 "utc","event","direction","entry_z","exit_z","entry_price","exit_price",
 "expected_net_rub","realized_net_rub","hold_min","reason"
]

def utc():
    return datetime.now(timezone.utc).isoformat()

def parse_num(s):
    if s is None:return None
    s=str(s).replace(" ","").replace(" ","").replace(",",".").strip()
    if s in ("","-","—"):return None
    try:return float(s)
    except:return None

def get_json(url,params=None):
    r=S.get(url,params=params or {},timeout=20);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def fut(secid):
    j=get_json(f"{ISS}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s or not m:return None
    a=s[0];b=m[0]
    def num(v):
        try:return float(v)
        except:return None
    ms=num(a.get("MINSTEP")) or 1.0
    sp=num(a.get("STEPPRICE")) or ms
    return {
      "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),"last":num(b.get("LAST")),
      "trades":num(b.get("NUMTRADES")) or 0,"volume":num(b.get("VOLTODAY")) or 0,
      "unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0,
      "scalper_fee":num(a.get("SCALPERFEE")) or 0.0
    }

def page_row():
    r=S.get(PAGE,timeout=25)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    # First try structured table rows.
    for tr in soup.find_all("tr"):
        cells=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if not cells:continue
        if cells[0].strip()==CODE:
            vals=cells
            # Expected visible columns:
            # ticker,last,bid,ask,high,low,amount,volume,trades,...
            return {
              "code":vals[0],
              "last":parse_num(vals[1]) if len(vals)>1 else None,
              "bid":parse_num(vals[2]) if len(vals)>2 else None,
              "ask":parse_num(vals[3]) if len(vals)>3 else None,
              "high":parse_num(vals[4]) if len(vals)>4 else None,
              "low":parse_num(vals[5]) if len(vals)>5 else None,
              "amount":parse_num(vals[6]) if len(vals)>6 else None,
              "volume_rub":parse_num(vals[7]) if len(vals)>7 else None,
              "trades":parse_num(vals[8]) if len(vals)>8 else None,
              "raw":vals
            }
    # Fallback: search text line around code.
    text=soup.get_text("\\n",strip=True)
    pos=text.find(CODE)
    if pos<0:
        raise RuntimeError(f"{CODE} not found on MOEX calendar spreads page")
    return {"code":CODE,"error":"row_parse_failed","context":text[pos:pos+500]}

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"history":[],"position":None}

def save_state(st):
    # Cap retained history.
    st["history"]=st.get("history",[])[-500:]
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append_csv(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def once(st):
    pr=page_row()
    if pr.get("error"):return {"utc":utc(),**pr}
    if pr["bid"] is None or pr["ask"] is None or pr["bid"]==0 or pr["ask"]==0:
        return {"utc":utc(),"error":"no two-sided calendar spread quote","page":pr}

    n=fut(NEAR);f=fut(FAR)
    if not n or not f or None in (n["bid"],n["ask"],f["bid"],f["ask"]):
        return {"utc":utc(),"error":"leg quote missing","page":pr}

    synthetic_bid=f["bid"]-n["ask"]
    synthetic_ask=f["ask"]-n["bid"]
    synthetic_mid=(synthetic_bid+synthetic_ask)/2
    atomic_mid=(pr["bid"]+pr["ask"])/2
    dislocation=atomic_mid-synthetic_mid

    # Conservative cost for crossing both atomic and synthetic constructions:
    # treat atomic as if it incurred both leg scalper fees, plus synthetic leg fees.
    leg_scalper=(n["scalper_fee"]+f["scalper_fee"])
    lock_cost=2*leg_scalper+EXTRA_RUB
    lock_a=(pr["bid"]-synthetic_ask)*n["unit"]-lock_cost
    lock_b=(synthetic_bid-pr["ask"])*n["unit"]-lock_cost

    history=st.setdefault("history",[])
    hist_vals=[float(x["atomic_mid"]) for x in history[-LOOKBACK:] if x.get("atomic_mid") is not None]
    mean=statistics.fmean(hist_vals) if len(hist_vals)>=LOOKBACK else None
    sd=statistics.pstdev(hist_vals) if len(hist_vals)>=LOOKBACK else None
    z=(atomic_mid-mean)/sd if mean is not None and sd and sd>1e-12 else None

    atomic_width=(pr["ask"]-pr["bid"])*n["unit"]
    atomic_rt_fee=2*leg_scalper
    meanrev_cost=atomic_width+atomic_rt_fee+EXTRA_RUB
    expected_sell=((pr["bid"]-mean)*n["unit"]-meanrev_cost) if mean is not None else None
    expected_buy=((mean-pr["ask"])*n["unit"]-meanrev_cost) if mean is not None else None

    pos=st.get("position")
    if pos and z is not None:
        hold=(time.time()-pos["opened_ts"])/60
        if pos["direction"]=="SELL":
            pnl=(pos["entry_price"]-pr["ask"])*n["unit"]-atomic_rt_fee-EXTRA_RUB
            exit_px=pr["ask"]
        else:
            pnl=(pr["bid"]-pos["entry_price"])*n["unit"]-atomic_rt_fee-EXTRA_RUB
            exit_px=pr["bid"]
        reason=None
        if abs(z)<=EXIT_Z:reason="CONVERGENCE"
        elif abs(z)>=STOP_Z:reason="Z_STOP"
        elif hold>=MAX_HOLD_MIN:reason="MAX_HOLD"
        if reason:
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{
              "utc":utc(),"event":"CLOSE","direction":pos["direction"],
              "entry_z":pos["entry_z"],"exit_z":z,"entry_price":pos["entry_price"],
              "exit_price":exit_px,"expected_net_rub":pos["expected_net_rub"],
              "realized_net_rub":pnl,"hold_min":hold,"reason":reason
            })
            st["position"]=None
    elif not pos and z is not None:
        if z>=ENTRY_Z and expected_sell is not None and expected_sell>=MIN_EXPECTED_NET_RUB:
            st["position"]={"direction":"SELL","opened_ts":time.time(),"entry_z":z,
                            "entry_price":pr["bid"],"expected_net_rub":expected_sell}
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{
              "utc":utc(),"event":"OPEN","direction":"SELL","entry_z":z,
              "entry_price":pr["bid"],"expected_net_rub":expected_sell,
              "reason":"ATOMIC_SPREAD_HIGH"
            })
        elif z<=-ENTRY_Z and expected_buy is not None and expected_buy>=MIN_EXPECTED_NET_RUB:
            st["position"]={"direction":"BUY","opened_ts":time.time(),"entry_z":z,
                            "entry_price":pr["ask"],"expected_net_rub":expected_buy}
            append_csv(LEDGER_FILE,LEDGER_FIELDS,{
              "utc":utc(),"event":"OPEN","direction":"BUY","entry_z":z,
              "entry_price":pr["ask"],"expected_net_rub":expected_buy,
              "reason":"ATOMIC_SPREAD_LOW"
            })

    row={
      "utc":utc(),"atomic_bid":pr["bid"],"atomic_ask":pr["ask"],"atomic_mid":atomic_mid,
      "last":pr["last"],"volume_contracts":pr["amount"],"volume_rub":pr["volume_rub"],
      "trades":pr["trades"],"synthetic_bid":synthetic_bid,"synthetic_ask":synthetic_ask,
      "synthetic_mid":synthetic_mid,"atomic_minus_synth_mid":dislocation,
      "lock_sell_atomic_buy_synth_rub":lock_a,
      "lock_sell_synth_buy_atomic_rub":lock_b,
      "baseline_mean":mean,"baseline_sd":sd,"z":z,
      "expected_meanrev_sell_rub":expected_sell,
      "expected_meanrev_buy_rub":expected_buy,
      "position":st.get("position")
    }
    history.append({"utc":row["utc"],"atomic_mid":atomic_mid,
                    "atomic_bid":pr["bid"],"atomic_ask":pr["ask"]})
    return row

def main():
    st=load_state();samples=[]
    for i in range(SAMPLES):
        try:r=once(st)
        except Exception as e:r={"utc":utc(),"error":f"{type(e).__name__}: {e}"}
        samples.append(r)
        if "error" not in r:
            append_csv(TS_FILE,TS_FIELDS,r)
            save_state(st)
        if i<SAMPLES-1:time.sleep(SLEEP_SEC)
    report={"utc":utc(),"code":CODE,"samples":samples,"position":st.get("position"),
            "history_points":len(st.get("history",[]))}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"history_points":report["history_points"],
                      "last":samples[-1] if samples else None,
                      "position":st.get("position")},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
