from __future__ import annotations

import csv, json, statistics, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)

STATE_FILE=STATE_DIR/"op_gate_okx_maker_state.json"
LAST_FILE=STATE_DIR/"op_gate_okx_maker_last.json"
LEDGER_FILE=STATE_DIR/"op_gate_okx_maker_ledger.csv"
TS_FILE=STATE_DIR/"op_gate_okx_maker_timeseries.csv"

BASE="OP"
NOTIONAL=1000.0

# Current public base-tier assumptions. Account-specific fees replace these before MICRO_LIVE.
GATE_MAKER=0.0002
OKX_TAKER=0.0005
ROUNDTRIP_FEE_BPS=2*(GATE_MAKER+OKX_TAKER)*10000

ENTRY_Z=3.0
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=12*3600
MAX_MAKER_WAIT_SEC=15*60
EXTRA_BUFFER_BPS=8.0
MIN_EXPECTED_NET_BPS=5.0
MAX_QUOTE_GAP_MS=750

HIST_BARS=72
SAMPLES=20
SLEEP_SEC=5

LEDGER_FIELDS=[
 "utc","event","direction","gate_limit","gate_fill","okx_hedge","entry_z","exit_z",
 "expected_net_bps","funding_bps","realized_net_bps","hold_sec","maker_wait_sec","reason"
]

def utc(): return datetime.now(timezone.utc).isoformat()

def make(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    ms=[m for m in ex.markets.values()
        if m.get("swap") and m.get("linear") and m.get("active",True)
        and m.get("base")==BASE and m.get("quote")=="USDT"]
    if not ms: raise RuntimeError(f"{exid}: OP/USDT linear swap unavailable")
    return ex,ms[0]["symbol"]

def vwap(levels,quote):
    rem=quote;base=done=0.0
    for x in levels:
        px=float(x[0]); qty=float(x[1])
        q=px*qty; take=min(rem,q)
        done+=take; base+=take/px; rem-=take
        if rem<=1e-9: break
    if rem>1e-6 or base<=0:return None
    return done/base

def book(ex,sym):
    ob=ex.fetch_order_book(sym,50)
    bid=vwap(ob.get("bids") or [],NOTIONAL)
    ask=vwap(ob.get("asks") or [],NOTIONAL)
    if bid is None or ask is None: raise RuntimeError("insufficient depth")
    return bid,ask


def paired_books(gate,gsym,okx,osym):
    def timed(ex,sym):
        t0=int(time.time()*1000)
        bid,ask=book(ex,sym)
        t1=int(time.time()*1000)
        return {"bid":bid,"ask":ask,"mid_ts":(t0+t1)//2,"latency_ms":t1-t0}
    with ThreadPoolExecutor(max_workers=2) as pool:
        fg=pool.submit(timed,gate,gsym)
        fo=pool.submit(timed,okx,osym)
        g=fg.result();o=fo.result()
    gap=abs(g["mid_ts"]-o["mid_ts"])
    return g,o,gap

def hist(ex,sym):
    since=int((time.time()-10*86400)*1000)
    out=[];cursor=since
    for _ in range(5):
        rr=ex.fetch_ohlcv(sym,"1h",cursor,300)
        if not rr:break
        out.extend(rr)
        mx=max(int(x[0]) for x in rr)
        if mx<=cursor:break
        cursor=mx+3600000
        if cursor>=int(time.time()*1000)-3600000:break
    return {int(x[0]):{"close":float(x[4]),"volume":float(x[5] or 0)}
            for x in out if len(x)>=6}

def baseline(aex,asym,bex,bsym):
    A=hist(aex,asym);B=hist(bex,bsym)
    ts=[t for t in sorted(set(A)&set(B))
        if A[t]["volume"]>0 and B[t]["volume"]>0][-HIST_BARS:]
    xs=[(B[t]["close"]/A[t]["close"]-1)*10000 for t in ts]
    if len(xs)<36:raise RuntimeError(f"baseline too short: {len(xs)}")
    sd=statistics.pstdev(xs)
    if sd<=1e-9:raise RuntimeError("zero baseline variance")
    return {"mean":statistics.fmean(xs),"sd":sd,"n":len(xs)}

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"phase":"FLAT","pending":None,"position":None}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append(row):
    new=not LEDGER_FILE.exists()
    with LEDGER_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=LEDGER_FIELDS)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in LEDGER_FIELDS})

def append_ts(row):
    fields=list(row.keys())
    new=not TS_FILE.exists()
    with TS_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow(row)

def recent_trades(ex,sym,since_ms):
    try:
        rr=ex.fetch_trades(sym,since_ms,100)
    except Exception:
        return []
    out=[]
    for x in rr:
        try:
            out.append({"ts":int(x.get("timestamp") or 0),"price":float(x.get("price"))})
        except:pass
    return out

def maker_filled(side,limit_px,trades):
    # Conservative shadow fill: require a public trade THROUGH our price.
    # Merely touching best bid/ask is not treated as a fill.
    if side=="BUY":
        return any(t["price"] < limit_px for t in trades)
    return any(t["price"] > limit_px for t in trades)

def entry_expected(direction,gate_limit,okx_bid,okx_ask,mean):
    if direction=="LONG_GATE_SHORT_OKX":
        exec_basis=(okx_bid/gate_limit-1)*10000
        gross=exec_basis-mean
    else:
        exec_basis=(okx_ask/gate_limit-1)*10000
        gross=mean-exec_basis
    return gross-ROUNDTRIP_FEE_BPS-EXTRA_BUFFER_BPS,exec_basis


def funding_since(ex,sym,since_ms):
    if not ex.has.get("fetchFundingRateHistory"):
        return 0.0
    try:
        rr=ex.fetch_funding_rate_history(sym,since_ms,100)
        return sum(float(x.get("fundingRate") or 0.0)
                   for x in rr if int(x.get("timestamp") or 0)>since_ms)
    except Exception:
        return 0.0

def funding_pnl_bps(pos,gate,gsym,okx,osym):
    since=int(float(pos["opened_ts"])*1000)
    gf=funding_since(gate,gsym,since)
    of=funding_since(okx,osym,since)
    if pos["direction"]=="LONG_GATE_SHORT_OKX":
        return (-gf+of)*10000
    return (gf-of)*10000

def current_pnl(pos,gate_bid,gate_ask,okx_bid,okx_ask):
    if pos["direction"]=="LONG_GATE_SHORT_OKX":
        raw=((gate_bid/pos["gate_entry"]-1)+(1-okx_ask/pos["okx_entry"]))*10000
    else:
        raw=((1-gate_ask/pos["gate_entry"])+(okx_bid/pos["okx_entry"]-1))*10000
    return raw-ROUNDTRIP_FEE_BPS-EXTRA_BUFFER_BPS

def main():
    gate,gsym=make("gate")
    okx,osym=make("okx")
    bl=baseline(gate,gsym,okx,osym)
    st=load_state()
    samples=[]

    for sample_no in range(SAMPLES):
        now=time.time(); nowms=int(now*1000)
        gbook,obook,quote_gap_ms=paired_books(gate,gsym,okx,osym)
        gbid,gask=gbook["bid"],gbook["ask"]
        obid,oask=obook["bid"],obook["ask"]
        if quote_gap_ms>MAX_QUOTE_GAP_MS:
            row={"utc":utc(),"sample":sample_no,"phase":st["phase"],
                 "quote_gap_ms":quote_gap_ms,"event":"REJECT_STALE_PAIR"}
            samples.append(row);append_ts(row);save_state(st)
            if sample_no<SAMPLES-1:time.sleep(SLEEP_SEC)
            continue
        gmid=(gbid+gask)/2; omid=(obid+oask)/2
        x=(omid/gmid-1)*10000
        z=(x-bl["mean"])/bl["sd"]

        event=None
        if st["phase"]=="FLAT":
            if z>=ENTRY_Z:
                direction="LONG_GATE_SHORT_OKX"
                gate_side="BUY"
                gate_limit=gbid
            elif z<=-ENTRY_Z:
                direction="SHORT_GATE_LONG_OKX"
                gate_side="SELL"
                gate_limit=gask
            else:
                direction=None

            if direction:
                exp,exec_basis=entry_expected(direction,gate_limit,obid,oask,bl["mean"])
                if exp>=MIN_EXPECTED_NET_BPS:
                    st={"phase":"ENTRY_MAKER_WAIT",
                        "pending":{"direction":direction,"gate_side":gate_side,
                                   "gate_limit":gate_limit,"placed_ts":now,
                                   "placed_ms":nowms,"entry_z":z,
                                   "expected_net_bps":exp,
                                   "entry_exec_basis_bps":exec_basis},
                        "position":None}
                    event="ENTRY_MAKER_POSTED"
                    append({"utc":utc(),"event":event,"direction":direction,
                            "gate_limit":gate_limit,"entry_z":z,
                            "expected_net_bps":exp,"reason":"Z_SIGNAL"})

        elif st["phase"]=="ENTRY_MAKER_WAIT":
            p=st["pending"]; wait=now-p["placed_ts"]
            trades=recent_trades(gate,gsym,p["placed_ms"])
            if maker_filled(p["gate_side"],p["gate_limit"],trades):
                # Hedge immediately at current OKX taker price.
                okx_hedge=obid if p["direction"]=="LONG_GATE_SHORT_OKX" else oask
                st={"phase":"HEDGED","pending":None,
                    "position":{"direction":p["direction"],"opened_ts":now,
                                "gate_entry":p["gate_limit"],"okx_entry":okx_hedge,
                                "entry_z":p["entry_z"],
                                "expected_net_bps":p["expected_net_bps"]}}
                event="ENTRY_FILLED_HEDGED"
                append({"utc":utc(),"event":event,"direction":p["direction"],
                        "gate_limit":p["gate_limit"],"gate_fill":p["gate_limit"],
                        "okx_hedge":okx_hedge,"entry_z":p["entry_z"],
                        "expected_net_bps":p["expected_net_bps"],
                        "maker_wait_sec":wait,"reason":"TRADE_THROUGH_FILL"})
            elif wait>=MAX_MAKER_WAIT_SEC or abs(z)<ENTRY_Z*0.75:
                event="ENTRY_MAKER_CANCEL"
                append({"utc":utc(),"event":event,"direction":p["direction"],
                        "gate_limit":p["gate_limit"],"entry_z":p["entry_z"],
                        "expected_net_bps":p["expected_net_bps"],
                        "maker_wait_sec":wait,"reason":"TIMEOUT_OR_SIGNAL_FADE"})
                st={"phase":"FLAT","pending":None,"position":None}

        elif st["phase"]=="HEDGED":
            p=st["position"]; hold=now-p["opened_ts"]
            pnl=current_pnl(p,gbid,gask,obid,oask)
            exit_signal=abs(z)<=EXIT_Z or abs(z)>=STOP_Z or hold>=MAX_HOLD_SEC
            if exit_signal:
                # Close Gate leg as maker first; only after its fill will OKX be flattened.
                if p["direction"]=="LONG_GATE_SHORT_OKX":
                    side="SELL";limit_px=gask
                else:
                    side="BUY";limit_px=gbid
                reason=("CONVERGENCE" if abs(z)<=EXIT_Z else
                        "Z_STOP" if abs(z)>=STOP_Z else "MAX_HOLD")
                st={"phase":"EXIT_MAKER_WAIT",
                    "pending":{"direction":p["direction"],"gate_side":side,
                               "gate_limit":limit_px,"placed_ts":now,"placed_ms":nowms,
                               "exit_z":z,"reason":reason,
                               "position":p},
                    "position":p}
                event="EXIT_MAKER_POSTED"
                append({"utc":utc(),"event":event,"direction":p["direction"],
                        "gate_limit":limit_px,"entry_z":p["entry_z"],"exit_z":z,
                        "expected_net_bps":p["expected_net_bps"],
                        "realized_net_bps":pnl,"hold_sec":hold,"reason":reason})

        elif st["phase"]=="EXIT_MAKER_WAIT":
            p=st["pending"]; wait=now-p["placed_ts"]; pos=p["position"]
            trades=recent_trades(gate,gsym,p["placed_ms"])
            if maker_filled(p["gate_side"],p["gate_limit"],trades):
                # Gate close filled; immediately flatten OKX at taker.
                if pos["direction"]=="LONG_GATE_SHORT_OKX":
                    okx_exit=oask
                    raw=((p["gate_limit"]/pos["gate_entry"]-1)+(1-okx_exit/pos["okx_entry"]))*10000
                else:
                    okx_exit=obid
                    raw=((1-p["gate_limit"]/pos["gate_entry"])+(okx_exit/pos["okx_entry"]-1))*10000
                funding_bps=funding_pnl_bps(pos,gate,gsym,okx,osym)
                pnl=raw+funding_bps-ROUNDTRIP_FEE_BPS-EXTRA_BUFFER_BPS
                event="CLOSE_FILLED"
                append({"utc":utc(),"event":event,"direction":pos["direction"],
                        "gate_limit":p["gate_limit"],"gate_fill":p["gate_limit"],
                        "okx_hedge":okx_exit,"entry_z":pos["entry_z"],"exit_z":p["exit_z"],
                        "expected_net_bps":pos["expected_net_bps"],
                        "funding_bps":funding_bps,
                        "realized_net_bps":pnl,"hold_sec":now-pos["opened_ts"],
                        "maker_wait_sec":wait,"reason":p["reason"]})
                st={"phase":"FLAT","pending":None,"position":None}
            elif wait>=MAX_MAKER_WAIT_SEC:
                # Shadow safety: do not assume maker exit. Record unresolved; real engine would
                # invoke emergency taker flatten under its configured max-unhedged/exit policy.
                event="EXIT_MAKER_TIMEOUT"
                append({"utc":utc(),"event":event,"direction":pos["direction"],
                        "gate_limit":p["gate_limit"],"entry_z":pos["entry_z"],"exit_z":p["exit_z"],
                        "expected_net_bps":pos["expected_net_bps"],
                        "hold_sec":now-pos["opened_ts"],"maker_wait_sec":wait,
                        "reason":"MAKER_EXIT_NOT_FILLED"})
                st={"phase":"HEDGED","pending":None,"position":pos}

        row={"utc":utc(),"sample":sample_no,"phase":st["phase"],
             "gate_bid":gbid,"gate_ask":gask,"okx_bid":obid,"okx_ask":oask,
             "mid_basis_bps":x,"z":z,"baseline_mean_bps":bl["mean"],
             "baseline_sd_bps":bl["sd"],"quote_gap_ms":quote_gap_ms,
             "gate_latency_ms":gbook["latency_ms"],"okx_latency_ms":obook["latency_ms"],
             "event":event or ""}
        samples.append(row); append_ts(row); save_state(st)
        if sample_no<SAMPLES-1: time.sleep(SLEEP_SEC)

    report={"utc":utc(),"route":"OP|GATE_MAKER|OKX_TAKER",
            "fees":{"gate_maker":GATE_MAKER,"okx_taker":OKX_TAKER,
                    "roundtrip_fee_bps":ROUNDTRIP_FEE_BPS},
            "baseline":bl,"state":st,"samples":samples}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"route":report["route"],"baseline":bl,"state":st,
                      "last":samples[-1] if samples else None},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
