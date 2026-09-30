from __future__ import annotations

import csv, json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
CFG=ROOT/"event_route_config.json"
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"crypto_validated_fast_state.json"
LEDGER_FILE=STATE_DIR/"crypto_validated_fast_ledger.csv"
LAST_FILE=STATE_DIR/"crypto_validated_fast_last.json"
TS_FILE=STATE_DIR/"crypto_validated_fast_timeseries.csv"

NOTIONAL=1000.0
SAMPLES=6
SLEEP_SEC=10
HIST_BARS=72
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=3600
EXTRA_BUFFER_BPS=6.0

PERP_FEE={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008,"HTX":0.0005}
SPOT_FEE={"OKX":0.0010,"BITGET":0.0010,"GATE":0.0010,"MEXC":0.0005,"HTX":0.0020}

LEDGER_FIELDS=[
 "utc","event","kind","route_key","base","venue_a","venue_b","direction",
 "entry_z","exit_z","baseline_mean_bps","entry_exec_spread_bps",
 "exit_mid_spread_bps","expected_net_bps","realized_net_bps","hold_sec","reason"
]

def utc(): return datetime.now(timezone.utc).isoformat()

def load_cfg():
    return json.loads(CFG.read_text(encoding="utf-8"))

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"positions":{}}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append_csv(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def vwap(levels,quote):
    rem=quote;base=done=0.0
    for x in levels:
        px=float(x[0]);qty=float(x[1]);q=px*qty;take=min(rem,q)
        done+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None
    return done/base

def build_exchange(venue):
    ex=getattr(ccxt,venue.lower())({"enableRateLimit":True,"timeout":18000})
    ex.load_markets()
    return ex

def symbol(ex,base,kind):
    if kind=="PERP":
        ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
            and m.get("active",True) and m.get("base")==base and m.get("quote")=="USDT"]
    else:
        ms=[m for m in ex.markets.values() if m.get("spot") and m.get("active",True)
            and m.get("base")==base and m.get("quote")=="USDT"]
    return ms[0]["symbol"] if ms else None

def hist_basis(exa,sa,exb,sb):
    try:
        A=exa.fetch_ohlcv(sa,"1h",None,HIST_BARS)
        B=exb.fetch_ohlcv(sb,"1h",None,HIST_BARS)
    except Exception:
        return None
    ad={int(x[0]):float(x[4]) for x in A if len(x)>=5}
    bd={int(x[0]):float(x[4]) for x in B if len(x)>=5}
    ts=sorted(set(ad)&set(bd))[-HIST_BARS:]
    xs=[(bd[t]/ad[t]-1.0)*10000 for t in ts if ad[t]>0]
    if len(xs)<20:return None
    sd=statistics.pstdev(xs)
    if sd<=1e-9:return None
    return {"mean":statistics.fmean(xs),"sd":sd,"n":len(xs)}

def book(ex,sym,venue):
    try:
        ob=ex.fetch_order_book(sym,20 if venue=="HTX" else 50)
        if venue=="MEXC":time.sleep(0.25)
        return {"bid":vwap(ob.get("bids") or [],NOTIONAL),
                "ask":vwap(ob.get("asks") or [],NOTIONAL)}
    except Exception:
        return None

def fee_bps(kind,a,b):
    fm=PERP_FEE if kind=="PERP" else SPOT_FEE
    return 2*(fm.get(a,.0015)+fm.get(b,.0015))*10000

def mark(pos,A,B):
    if pos["direction"]=="LONG_A_SHORT_B":
        ret=(A["bid"]/pos["a_entry"]-1)+(1-B["ask"]/pos["b_entry"])
    else:
        ret=(1-A["ask"]/pos["a_entry"])+(B["bid"]/pos["b_entry"]-1)
    return ret*10000-pos["fee_bps"]-EXTRA_BUFFER_BPS

def choose_routes(cfg):
    rows=[]
    for kind,section in (("PERP",cfg.get("crypto_perp") or {}),
                         ("SPOT",cfg.get("crypto_spot") or {})):
        for key,r in section.items():
            h=r.get("holdout") or {}
            score=(float(h.get("median_net_bps") or 0)
                   * math.sqrt(max(1,int(h.get("n") or 1))))
            rows.append((score,kind,key,r))
    # enough breadth but keeps each run fast
    rows.sort(key=lambda x:x[0],reverse=True)
    return rows[:10]

def main():
    cfg=load_cfg()
    selected=choose_routes(cfg)
    state=load_state()

    venues=sorted(set(v for _,_,_,r in selected for v in (r["venue_a"],r["venue_b"])))
    exs={}
    for v in venues:
        try:exs[v]=build_exchange(v)
        except Exception:pass

    routes=[]
    for _,kind,key,r in selected:
        a,b=r["venue_a"],r["venue_b"];base=r["base"]
        if a not in exs or b not in exs:continue
        sa=symbol(exs[a],base,kind);sb=symbol(exs[b],base,kind)
        if not sa or not sb:continue
        bl=hist_basis(exs[a],sa,exs[b],sb)
        if not bl:continue
        routes.append({
          "kind":kind,"key":key,"base":base,"a":a,"b":b,
          "sa":sa,"sb":sb,"baseline":bl,
          "entry_z":float(r.get("entry_z") or 2.5),
          "fee_bps":fee_bps(kind,a,b)
        })

    samples=[]
    for sample_no in range(SAMPLES):
        now=time.time()
        for r in routes:
            A=book(exs[r["a"]],r["sa"],r["a"])
            B=book(exs[r["b"]],r["sb"],r["b"])
            if not A or not B or not A["bid"] or not A["ask"] or not B["bid"] or not B["ask"]:
                continue
            mid=((B["bid"]+B["ask"])/2)/((A["bid"]+A["ask"])/2)
            x=(mid-1)*10000
            z=(x-r["baseline"]["mean"])/r["baseline"]["sd"]
            hi=(B["bid"]/A["ask"]-1)*10000
            lo=(B["ask"]/A["bid"]-1)*10000
            exit_spread=((A["ask"]-A["bid"])/((A["ask"]+A["bid"])/2)
                         +(B["ask"]-B["bid"])/((B["ask"]+B["bid"])/2))*10000
            costs=r["fee_bps"]+exit_spread+EXTRA_BUFFER_BPS
            expected_high=hi-r["baseline"]["mean"]-costs
            expected_low=r["baseline"]["mean"]-lo-costs
            pos=state["positions"].get(r["key"])

            row={"utc":utc(),"sample":sample_no,"kind":r["kind"],"route_key":r["key"],
                 "base":r["base"],"venue_a":r["a"],"venue_b":r["b"],
                 "mid_basis_bps":x,"z":z,"baseline_mean_bps":r["baseline"]["mean"],
                 "baseline_sd_bps":r["baseline"]["sd"],"exec_high_bps":hi,"exec_low_bps":lo,
                 "fee_bps":r["fee_bps"],"exit_spread_bps":exit_spread,
                 "expected_high_bps":expected_high,"expected_low_bps":expected_low,
                 "position":pos["direction"] if pos else "FLAT"}
            samples.append(row)
            append_csv(TS_FILE,list(row.keys()),row)

            if pos:
                pnl=mark(pos,A,B)
                hold=now-pos["opened_ts"]
                reason=None
                if abs(z)<=EXIT_Z:reason="CONVERGENCE"
                elif abs(z)>=STOP_Z:reason="Z_STOP"
                elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
                if reason:
                    append_csv(LEDGER_FILE,LEDGER_FIELDS,{
                      "utc":utc(),"event":"CLOSE","kind":r["kind"],"route_key":r["key"],
                      "base":r["base"],"venue_a":r["a"],"venue_b":r["b"],
                      "direction":pos["direction"],"entry_z":pos["entry_z"],"exit_z":z,
                      "baseline_mean_bps":r["baseline"]["mean"],
                      "entry_exec_spread_bps":pos["entry_exec_spread_bps"],
                      "exit_mid_spread_bps":x,"expected_net_bps":pos["expected_net_bps"],
                      "realized_net_bps":pnl,"hold_sec":hold,"reason":reason
                    })
                    del state["positions"][r["key"]]
            else:
                direction=None;expected=None
                if z>=r["entry_z"] and expected_high>0:
                    direction="LONG_A_SHORT_B";expected=expected_high
                    a_entry=A["ask"];b_entry=B["bid"];esp=hi
                elif z<=-r["entry_z"] and expected_low>0:
                    direction="SHORT_A_LONG_B";expected=expected_low
                    a_entry=A["bid"];b_entry=B["ask"];esp=lo
                if direction:
                    state["positions"][r["key"]]={
                      "direction":direction,"opened_ts":now,"entry_z":z,
                      "entry_exec_spread_bps":esp,"expected_net_bps":expected,
                      "a_entry":a_entry,"b_entry":b_entry,"fee_bps":r["fee_bps"]
                    }
                    append_csv(LEDGER_FILE,LEDGER_FIELDS,{
                      "utc":utc(),"event":"OPEN","kind":r["kind"],"route_key":r["key"],
                      "base":r["base"],"venue_a":r["a"],"venue_b":r["b"],
                      "direction":direction,"entry_z":z,
                      "baseline_mean_bps":r["baseline"]["mean"],
                      "entry_exec_spread_bps":esp,"expected_net_bps":expected,
                      "reason":"VALIDATED_FAST_DISLOCATION"
                    })
        save_state(state)
        if sample_no<SAMPLES-1:time.sleep(SLEEP_SEC)

    report={"utc":utc(),"selected_routes":[
              {"kind":r["kind"],"key":r["key"],"base":r["base"],"a":r["a"],"b":r["b"],
               "entry_z":r["entry_z"],"baseline":r["baseline"]} for r in routes],
            "samples":samples[-50:],"open_positions":state["positions"]}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"routes":len(routes),"open_positions":len(state["positions"]),
                      "last_samples":samples[-10:]},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
