from __future__ import annotations

import csv, json, math, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
STATE_FILE=STATE_DIR/"crypto_spot_event_state.json"
SCAN_FILE=STATE_DIR/"crypto_spot_event_last_scan.json"
LEDGER_FILE=STATE_DIR/"crypto_spot_event_ledger.csv"

VENUES=["okx","bitget","gate","mexc","htx"]
ASSETS=["BTC","ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH",
        "SUI","HBAR","WIF","APT","ARB","OP","UNI","DOT","FIL","ICP"]

FEE={"OKX":0.0010,"BITGET":0.0010,"GATE":0.0010,"MEXC":0.0005,"HTX":0.0020}
NOTIONAL=1000.0
ALPHA=0.10
MIN_BASELINE_SCANS=8
ENTRY_Z=2.5
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_MIN=720
MIN_EXPECTED_NET_BPS=12.0
EXTRA_BUFFER_BPS=8.0

LEDGER_FIELDS=[
 "utc","event","key","base","buy_provider","sell_provider","entry_z","exit_z",
 "baseline_mean_bps","entry_exec_spread_bps","exit_mid_spread_bps",
 "expected_net_bps","realized_net_bps","hold_min","reason"
]

def utc(): return datetime.now(timezone.utc).isoformat()

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"stats":{},"positions":{}}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")

def append_ledger(row):
    new=not LEDGER_FILE.exists()
    with LEDGER_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=LEDGER_FIELDS)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in LEDGER_FIELDS})

def vwap(levels,quote):
    rem=quote;base=done=0.0
    for x in levels:
        px=float(x[0]);qty=float(x[1]);q=px*qty;take=min(rem,q)
        done+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None
    return done/base

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    pairs={}
    for b in ASSETS:
        ms=[m for m in ex.markets.values()
            if m.get("spot") and m.get("active",True)
            and m.get("base")==b and m.get("quote")=="USDT"]
        if ms:pairs[b]=ms[0]["symbol"]
    return exid.upper(),ex,pairs

def snapshot():
    snap={};coverage=[];errors=[]
    for exid in VENUES:
        try:
            name,ex,pairs=build(exid)
            coverage.append({"provider":name,"ok":True,"pairs":len(pairs)})
            for b,sym in pairs.items():
                try:
                    ob=ex.fetch_order_book(sym,20 if name=="HTX" else 50)
                    if name=="MEXC":time.sleep(0.35)
                    ask=vwap(ob.get("asks") or [],NOTIONAL)
                    bid=vwap(ob.get("bids") or [],NOTIONAL)
                    if ask and bid:
                        snap[(name,b)]={"ask":ask,"bid":bid,"mid":(ask+bid)/2,"symbol":sym}
                except Exception as e:
                    errors.append({"provider":name,"base":b,"error":f"{type(e).__name__}: {e}"[:500]})
        except Exception as e:
            coverage.append({"provider":exid.upper(),"ok":False,"error":f"{type(e).__name__}: {e}"[:500]})
    return snap,coverage,errors

def stat_before_update(state,key,x):
    st=state["stats"].get(key)
    if not st:
        st={"n":1,"mean":x,"var":0.0};state["stats"][key]=st
        return st,None
    n=int(st["n"]);mean=float(st["mean"]);var=max(float(st.get("var",0)),0.0)
    sd=math.sqrt(var) if n>=MIN_BASELINE_SCANS and var>1e-12 else None
    z=(x-mean)/sd if sd else None
    delta=x-mean
    st["mean"]=mean+ALPHA*delta
    st["var"]=(1-ALPHA)*(var+ALPHA*delta*delta)
    st["n"]=n+1
    return st,z

def relation(a_name,b_name,base,A,B):
    x=(B["mid"]/A["mid"]-1)*10000
    hi=(B["bid"]/A["ask"]-1)*10000
    lo=(B["ask"]/A["bid"]-1)*10000
    four_trade_fee=2*(FEE.get(a_name,.0015)+FEE.get(b_name,.0015))*10000
    exit_spread=((A["ask"]-A["bid"])/A["mid"]+(B["ask"]-B["bid"])/B["mid"])*10000
    return {"key":f"SPOT:{base}:{a_name}:{b_name}","base":base,"a":a_name,"b":b_name,
            "x":x,"hi":hi,"lo":lo,"fee_bps":four_trade_fee,
            "exit_spread_bps":exit_spread,
            "a_bid":A["bid"],"a_ask":A["ask"],"b_bid":B["bid"],"b_ask":B["ask"]}

def mark_pnl(pos,r):
    if pos["direction"]=="BUY_A_SELL_B":
        ret=(r["a_bid"]/pos["a_entry"]-1)+(1-r["b_ask"]/pos["b_entry"])
    else:
        ret=(1-r["a_ask"]/pos["a_entry"])+(r["b_bid"]/pos["b_entry"]-1)
    return ret*10000-pos["fee_bps"]-EXTRA_BUFFER_BPS

def main():
    state=load_state()
    snap,coverage,errors=snapshot()
    rels=[]
    for base in ASSETS:
        avail=[(n,d) for (n,b),d in snap.items() if b==base]
        for i in range(len(avail)):
            for j in range(i+1,len(avail)):
                a,A=avail[i];b,B=avail[j]
                rels.append(relation(a,b,base,A,B))

    current={r["key"]:r for r in rels}
    opened=[];closed=[];scans=[]

    for key,pos in list(state["positions"].items()):
        r=current.get(key)
        if not r:continue
        st=state["stats"].get(key)
        sd=math.sqrt(max(float(st.get("var",0)),0)) if st and st.get("var",0)>0 else None
        z=(r["x"]-float(st["mean"]))/sd if st and sd else None
        pnl=mark_pnl(pos,r);hold=(time.time()-pos["opened_ts"])/60
        reason=None
        if z is not None and abs(z)<=EXIT_Z:reason="CONVERGENCE"
        elif z is not None and abs(z)>=STOP_Z:reason="Z_STOP"
        elif hold>=MAX_HOLD_MIN:reason="MAX_HOLD"
        if reason:
            append_ledger({"utc":utc(),"event":"CLOSE","key":key,"base":pos["base"],
                           "buy_provider":pos["buy_provider"],"sell_provider":pos["sell_provider"],
                           "entry_z":pos["entry_z"],"exit_z":z,
                           "baseline_mean_bps":st["mean"],"entry_exec_spread_bps":pos["entry_exec_spread_bps"],
                           "exit_mid_spread_bps":r["x"],"expected_net_bps":pos["expected_net_bps"],
                           "realized_net_bps":pnl,"hold_min":hold,"reason":reason})
            closed.append({"key":key,"realized_net_bps":pnl,"reason":reason})
            del state["positions"][key]

    for r in rels:
        st,z=stat_before_update(state,r["key"],r["x"])
        mean=float(st["mean"]);sd=math.sqrt(max(float(st["var"]),0)) if st["var"]>0 else None

        if z is not None and abs(z)>=ENTRY_Z and r["key"] not in state["positions"]:
            costs=r["fee_bps"]+r["exit_spread_bps"]+EXTRA_BUFFER_BPS
            direction=None;expected=None;buy=None;sell=None;exec_spread=None
            if z>0:
                gross=r["hi"]-mean
                if gross>costs:
                    direction="BUY_A_SELL_B";buy=r["a"];sell=r["b"];exec_spread=r["hi"];expected=gross-costs
                    a_entry=r["a_ask"];b_entry=r["b_bid"]
            else:
                gross=mean-r["lo"]
                if gross>costs:
                    direction="SELL_A_BUY_B";buy=r["b"];sell=r["a"];exec_spread=r["lo"];expected=gross-costs
                    a_entry=r["a_bid"];b_entry=r["b_ask"]

            if direction and expected is not None and expected>=MIN_EXPECTED_NET_BPS:
                pos={"key":r["key"],"base":r["base"],"direction":direction,
                     "buy_provider":buy,"sell_provider":sell,"opened_ts":time.time(),
                     "entry_z":z,"entry_exec_spread_bps":exec_spread,
                     "expected_net_bps":expected,"fee_bps":r["fee_bps"],
                     "a_entry":a_entry,"b_entry":b_entry}
                state["positions"][r["key"]]=pos
                append_ledger({"utc":utc(),"event":"OPEN","key":r["key"],"base":r["base"],
                               "buy_provider":buy,"sell_provider":sell,"entry_z":z,
                               "baseline_mean_bps":mean,"entry_exec_spread_bps":exec_spread,
                               "expected_net_bps":expected,"reason":"PREFUNDED_SPOT_DISLOCATION"})
                opened.append(pos)

        scans.append({"key":r["key"],"base":r["base"],"venue_a":r["a"],"venue_b":r["b"],
                      "mid_basis_bps":r["x"],"baseline_mean_bps":mean,"baseline_sd_bps":sd,
                      "z":z,"n":st["n"],"four_trade_fee_bps":r["fee_bps"],
                      "exit_spread_bps":r["exit_spread_bps"],"open":r["key"] in state["positions"]})

    save_state(state)
    report={"utc":utc(),"notional":NOTIONAL,"coverage":coverage,"errors":errors[:50],
            "relations":len(rels),"opened":opened,"closed":closed,"open_positions":state["positions"],
            "top_anomalies":sorted([x for x in scans if x["z"] is not None],
                                   key=lambda x:abs(x["z"]),reverse=True)[:40]}
    SCAN_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"relations":len(rels),"opened":len(opened),"closed":closed,
                      "top":report["top_anomalies"][:10]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
