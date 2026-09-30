from __future__ import annotations

import csv, json, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state";STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"crypto_tight_fast_state.json"
TS_FILE=STATE_DIR/"crypto_tight_fast_timeseries.csv"
LEDGER_FILE=STATE_DIR/"crypto_tight_fast_ledger.csv"
LAST_FILE=STATE_DIR/"crypto_tight_fast_last.json"

ROUTES=[
 {"base":"OP","a":"GATE","b":"OKX","entry_z":3.0},
 {"base":"OP","a":"BITGET","b":"GATE","entry_z":3.0},
 {"base":"OP","a":"GATE","b":"MEXC","entry_z":3.0},
 {"base":"WIF","a":"BITGET","b":"GATE","entry_z":3.0},
 {"base":"DOT","a":"BITGET","b":"GATE","entry_z":3.0},
]
FEE={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008}
NOTIONAL=1000.0
SAMPLES=6
SLEEP_SEC=10
HIST_BARS=72
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=12*3600
EXTRA_BUFFER_BPS=8.0

FIELDS=["utc","sample","route","base","venue_a","venue_b","mid_basis_bps","z",
        "baseline_mean_bps","baseline_sd_bps","exec_high_bps","exec_low_bps",
        "fee_bps","exit_spread_bps","expected_high_bps","expected_low_bps","position"]
LEDGER_FIELDS=["utc","event","route","base","direction","entry_z","exit_z",
               "entry_exec_bps","expected_net_bps","realized_net_bps","hold_sec","reason"]

def utc():return datetime.now(timezone.utc).isoformat()

def make(venue):
    ex=getattr(ccxt,venue.lower())({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    return ex

def symbol(ex,base):
    ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
        and m.get("active",True) and m.get("base")==base and m.get("quote")=="USDT"]
    return ms[0]["symbol"] if ms else None

def vwap(levels,quote):
    rem=quote;base=done=0.0
    for x in levels:
        px=float(x[0]);qty=float(x[1]);q=px*qty;take=min(rem,q)
        done+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None
    return done/base

def book(ex,sym):
    ob=ex.fetch_order_book(sym,50)
    bid=vwap(ob.get("bids") or [],NOTIONAL)
    ask=vwap(ob.get("asks") or [],NOTIONAL)
    if bid is None or ask is None:raise RuntimeError("insufficient depth")
    return {"bid":bid,"ask":ask}

def hist(ex,sym):
    rr=ex.fetch_ohlcv(sym,"1h",None,HIST_BARS)
    return {int(x[0]):{"close":float(x[4]),"volume":float(x[5] or 0)} for x in rr if len(x)>=6}

def baseline(aex,asym,bex,bsym):
    A=hist(aex,asym);B=hist(bex,bsym)
    ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0][-HIST_BARS:]
    xs=[(B[t]["close"]/A[t]["close"]-1)*10000 for t in ts]
    if len(xs)<24:raise RuntimeError(f"baseline too short {len(xs)}")
    sd=statistics.pstdev(xs)
    if sd<=1e-9:raise RuntimeError("zero baseline sd")
    return {"mean":statistics.fmean(xs),"sd":sd,"n":len(xs)}

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"positions":{}}

def save_state(s):STATE_FILE.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")

def append(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def main():
    venues=sorted(set(v for r in ROUTES for v in (r["a"],r["b"])))
    exs={v:make(v) for v in venues}
    prepared=[]
    for r in ROUTES:
        sa=symbol(exs[r["a"]],r["base"]);sb=symbol(exs[r["b"]],r["base"])
        if not sa or not sb:continue
        bl=baseline(exs[r["a"]],sa,exs[r["b"]],sb)
        prepared.append({**r,"sa":sa,"sb":sb,"baseline":bl,
                         "fee_bps":2*(FEE[r["a"]]+FEE[r["b"]])*10000})
    state=load_state();samples=[]

    for sample_no in range(SAMPLES):
        now=time.time()
        for r in prepared:
            key=f"{r['base']}|{r['a']}|{r['b']}"
            try:
                A=book(exs[r["a"]],r["sa"]);B=book(exs[r["b"]],r["sb"])
                amid=(A["bid"]+A["ask"])/2;bmid=(B["bid"]+B["ask"])/2
                x=(bmid/amid-1)*10000
                z=(x-r["baseline"]["mean"])/r["baseline"]["sd"]
                hi=(B["bid"]/A["ask"]-1)*10000
                lo=(B["ask"]/A["bid"]-1)*10000
                exit_spread=((A["ask"]-A["bid"])/amid+(B["ask"]-B["bid"])/bmid)*10000
                costs=r["fee_bps"]+exit_spread+EXTRA_BUFFER_BPS
                eh=hi-r["baseline"]["mean"]-costs
                el=r["baseline"]["mean"]-lo-costs
                pos=state["positions"].get(key)

                if pos:
                    hold=now-pos["opened_ts"]
                    if pos["direction"]=="LONG_A_SHORT_B":
                        pnl=((A["bid"]/pos["a_entry"]-1)+(1-B["ask"]/pos["b_entry"]))*10000-r["fee_bps"]-EXTRA_BUFFER_BPS
                    else:
                        pnl=((1-A["ask"]/pos["a_entry"])+(B["bid"]/pos["b_entry"]-1))*10000-r["fee_bps"]-EXTRA_BUFFER_BPS
                    reason=None
                    if abs(z)<=EXIT_Z:reason="CONVERGENCE"
                    elif abs(z)>=STOP_Z:reason="Z_STOP"
                    elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
                    if reason:
                        append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":"CLOSE","route":key,"base":r["base"],
                          "direction":pos["direction"],"entry_z":pos["entry_z"],"exit_z":z,
                          "entry_exec_bps":pos["entry_exec_bps"],"expected_net_bps":pos["expected_net_bps"],
                          "realized_net_bps":pnl,"hold_sec":hold,"reason":reason})
                        del state["positions"][key]
                else:
                    direction=None;expected=None
                    if z>=r["entry_z"] and eh>0:
                        direction="LONG_A_SHORT_B";expected=eh
                        a_entry=A["ask"];b_entry=B["bid"];esp=hi
                    elif z<=-r["entry_z"] and el>0:
                        direction="SHORT_A_LONG_B";expected=el
                        a_entry=A["bid"];b_entry=B["ask"];esp=lo
                    if direction:
                        state["positions"][key]={"direction":direction,"opened_ts":now,"entry_z":z,
                          "entry_exec_bps":esp,"expected_net_bps":expected,"a_entry":a_entry,"b_entry":b_entry}
                        append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":"OPEN","route":key,"base":r["base"],
                          "direction":direction,"entry_z":z,"entry_exec_bps":esp,
                          "expected_net_bps":expected,"reason":"TIGHT_VENUE_LIVE_DISLOCATION"})

                row={"utc":utc(),"sample":sample_no,"route":key,"base":r["base"],"venue_a":r["a"],"venue_b":r["b"],
                     "mid_basis_bps":x,"z":z,"baseline_mean_bps":r["baseline"]["mean"],
                     "baseline_sd_bps":r["baseline"]["sd"],"exec_high_bps":hi,"exec_low_bps":lo,
                     "fee_bps":r["fee_bps"],"exit_spread_bps":exit_spread,
                     "expected_high_bps":eh,"expected_low_bps":el,
                     "position":(state["positions"].get(key) or {}).get("direction","FLAT")}
                samples.append(row);append(TS_FILE,FIELDS,row)
            except Exception as e:
                samples.append({"utc":utc(),"sample":sample_no,"route":key,"error":f"{type(e).__name__}: {e}"})
        save_state(state)
        if sample_no<SAMPLES-1:time.sleep(SLEEP_SEC)

    report={"utc":utc(),"notional":NOTIONAL,
            "routes":[{"key":f"{r['base']}|{r['a']}|{r['b']}","entry_z":r["entry_z"],"baseline":r["baseline"]} for r in prepared],
            "samples":samples[-60:],"open_positions":state["positions"],
            "positive_executable_samples":sum(
                max(float(x.get("expected_high_bps",-1e9)),float(x.get("expected_low_bps",-1e9)))>0
                for x in samples if "error" not in x)}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"routes":len(prepared),"positive_executable_samples":report["positive_executable_samples"],
                      "open_positions":len(state["positions"])},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
