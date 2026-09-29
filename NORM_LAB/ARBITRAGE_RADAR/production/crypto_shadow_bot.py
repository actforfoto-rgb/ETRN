from __future__ import annotations

import csv, json, math, os, statistics, time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "shadow_state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "crypto_state.json"
LEDGER_FILE = STATE_DIR / "crypto_ledger.csv"
SNAPSHOT_FILE = STATE_DIR / "crypto_last_scan.json"

VENUES = ["okx","bitget","gate","mexc","htx"]
ASSETS = [
    "BTC","ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH",
    "SUI","TRX","TON","HBAR","PEPE","WIF","APT","ARB","OP","UNI",
    "DOT","NEAR","FIL","AAVE","ATOM","ETC","ICP","INJ","XLM","SHIB"
]
FEE = {"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008,"HTX":0.0005}

NOTIONAL = 1000.0
EXECUTION_BUFFER_BPS = 4.0
ENTRY_MIN_NET1_BPS = 5.0
ENTRY_MIN_FUNDING_BPS = 1.0
MAX_OPEN_PER_ASSET = 1
MAX_HOLD_HOURS = 72
TAKE_NET_BPS = 12.0
STOP_NET_BPS = -35.0

LEDGER_FIELDS = [
    "utc","event","base","long_provider","short_provider","notional",
    "long_price","short_price","funding_bps","basis_pnl_bps","fees_bps",
    "net_bps","hold_hours","reason"
]

def utc():
    return datetime.now(timezone.utc).isoformat()

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"positions":{},"last_run":None}

def save_state(s):
    s["last_run"]=utc()
    STATE_FILE.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding="utf-8")

def append_ledger(row):
    new=not LEDGER_FILE.exists()
    with LEDGER_FILE.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=LEDGER_FIELDS)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in LEDGER_FIELDS})

def vwap(levels, quote_amount):
    rem=quote_amount;base=0.0;done=0.0
    for x in levels:
        px=float(x[0]); qty=float(x[1])
        q=px*qty; take=min(rem,q)
        done+=take; base+=take/px; rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None
    return done/base

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    pairs={}
    for b in ASSETS:
        ms=[m for m in ex.markets.values()
            if m.get("swap") and m.get("linear") and m.get("active",True)
            and m.get("base")==b and m.get("quote")=="USDT"]
        if ms:pairs[b]=ms[0]["symbol"]
    return exid.upper(),ex,pairs

def current_funding(ex,symbol):
    try:
        if ex.has.get("fetchFundingRate"):
            r=ex.fetch_funding_rate(symbol)
            return float(r.get("fundingRate") or 0.0), int(r.get("fundingTimestamp") or 0)
    except Exception:
        pass
    return 0.0,0

def actual_funding_since(ex,symbol,since_ms):
    if not ex.has.get("fetchFundingRateHistory"):
        return 0.0
    try:
        rr=ex.fetch_funding_rate_history(symbol,since_ms,100)
        return sum(float(x.get("fundingRate") or 0.0) for x in rr
                   if int(x.get("timestamp") or 0)>since_ms)
    except Exception:
        return 0.0

def snapshot():
    providers={}
    coverage=[]
    for exid in VENUES:
        try:
            name,ex,pairs=build(exid)
            providers[name]=(ex,pairs)
            coverage.append({"provider":name,"ok":True,"pairs":len(pairs)})
        except Exception as e:
            coverage.append({"provider":exid.upper(),"ok":False,
                             "error":f"{type(e).__name__}: {e}"[:500]})
    snap={}
    errors=[]
    for name,(ex,pairs) in providers.items():
        for b,sym in pairs.items():
            try:
                lim=20 if name=="HTX" else 50
                ob=ex.fetch_order_book(sym,lim)
                fr,fts=current_funding(ex,sym)
                ask=vwap(ob["asks"],NOTIONAL)
                bid=vwap(ob["bids"],NOTIONAL)
                if ask and bid:
                    snap[(name,b)]={"ask":ask,"bid":bid,"funding":fr,
                                    "funding_ts":fts,"symbol":sym}
            except Exception as e:
                errors.append({"provider":name,"base":b,
                               "error":f"{type(e).__name__}: {e}"[:500]})
    return providers,snap,coverage,errors

def scan_candidates(snap):
    rows=[]
    for b in ASSETS:
        avail=[(n,d) for (n,x),d in snap.items() if x==b]
        for ln,L in avail:
            for sn,S in avail:
                if ln==sn:continue
                gross=(S["bid"]/L["ask"]-1.0)*10000
                fund=(-L["funding"]+S["funding"])*10000
                fees=2*(FEE.get(ln,.0007)+FEE.get(sn,.0007))*10000
                net1=gross+fund-fees-EXECUTION_BUFFER_BPS
                rows.append({
                    "base":b,"long_provider":ln,"short_provider":sn,
                    "long_ask":L["ask"],"short_bid":S["bid"],
                    "long_funding_bps":L["funding"]*10000,
                    "short_funding_bps":S["funding"]*10000,
                    "funding_capture_bps":fund,"gross_basis_bps":gross,
                    "fees_bps":fees,"screen_net1_bps":net1,
                    "long_symbol":L["symbol"],"short_symbol":S["symbol"]
                })
    rows.sort(key=lambda x:x["screen_net1_bps"],reverse=True)
    return rows

def evaluate_open(pos,providers,snap):
    b=pos["base"];ln=pos["long_provider"];sn=pos["short_provider"]
    L=snap.get((ln,b));S=snap.get((sn,b))
    if not L or not S:return None
    long_ret=L["bid"]/pos["long_entry"]-1.0
    short_ret=1.0-S["ask"]/pos["short_entry"]
    basis_pnl=(long_ret+short_ret)*10000
    since=int(pos["entry_ms"])
    fund=0.0
    try:
        lex=providers[ln][0]; sex=providers[sn][0]
        fund=(-actual_funding_since(lex,pos["long_symbol"],since)
              +actual_funding_since(sex,pos["short_symbol"],since))*10000
    except Exception:
        pass
    fees=pos["fees_bps"]
    net=basis_pnl+fund-fees-EXECUTION_BUFFER_BPS
    hold=(int(time.time()*1000)-since)/3600000
    return {"basis_pnl_bps":basis_pnl,"funding_bps":fund,"net_bps":net,
            "hold_hours":hold,"long_exit":L["bid"],"short_exit":S["ask"]}

def main():
    state=load_state()
    providers,snap,coverage,errors=snapshot()
    candidates=scan_candidates(snap)

    closed=[]
    for key,pos in list(state["positions"].items()):
        ev=evaluate_open(pos,providers,snap)
        if not ev:continue
        reason=None
        if ev["net_bps"]>=TAKE_NET_BPS:reason="TAKE_NET"
        elif ev["net_bps"]<=STOP_NET_BPS:reason="STOP_NET"
        elif ev["hold_hours"]>=MAX_HOLD_HOURS:reason="MAX_HOLD"
        else:
            L=snap.get((pos["long_provider"],pos["base"]))
            S=snap.get((pos["short_provider"],pos["base"]))
            if L and S:
                curfund=(-L["funding"]+S["funding"])*10000
                if curfund<=0 and ev["net_bps"]>0:reason="FUNDING_EDGE_GONE"
        if reason:
            append_ledger({
                "utc":utc(),"event":"CLOSE","base":pos["base"],
                "long_provider":pos["long_provider"],"short_provider":pos["short_provider"],
                "notional":NOTIONAL,"long_price":ev["long_exit"],"short_price":ev["short_exit"],
                "funding_bps":ev["funding_bps"],"basis_pnl_bps":ev["basis_pnl_bps"],
                "fees_bps":pos["fees_bps"],"net_bps":ev["net_bps"],
                "hold_hours":ev["hold_hours"],"reason":reason
            })
            closed.append({"key":key,"net_bps":ev["net_bps"],"reason":reason})
            del state["positions"][key]

    occupied={p["base"] for p in state["positions"].values()}
    opened=[]
    for c in candidates:
        if c["base"] in occupied:continue
        if c["screen_net1_bps"]<ENTRY_MIN_NET1_BPS:break
        if c["funding_capture_bps"]<ENTRY_MIN_FUNDING_BPS:continue
        key=c["base"]
        pos={
            "base":c["base"],"long_provider":c["long_provider"],"short_provider":c["short_provider"],
            "long_entry":c["long_ask"],"short_entry":c["short_bid"],
            "long_symbol":c["long_symbol"],"short_symbol":c["short_symbol"],
            "fees_bps":c["fees_bps"],"entry_ms":int(time.time()*1000),
            "entry_utc":utc(),"entry_screen_net1_bps":c["screen_net1_bps"],
            "entry_funding_capture_bps":c["funding_capture_bps"]
        }
        state["positions"][key]=pos
        occupied.add(c["base"])
        append_ledger({
            "utc":utc(),"event":"OPEN","base":c["base"],
            "long_provider":c["long_provider"],"short_provider":c["short_provider"],
            "notional":NOTIONAL,"long_price":c["long_ask"],"short_price":c["short_bid"],
            "funding_bps":0,"basis_pnl_bps":c["gross_basis_bps"],
            "fees_bps":c["fees_bps"],"net_bps":c["screen_net1_bps"],
            "hold_hours":0,"reason":"ENTRY_SCREEN"
        })
        opened.append(pos)

    save_state(state)
    report={
        "utc":utc(),"mode":"SHADOW_ONLY","notional":NOTIONAL,
        "coverage":coverage,"errors":errors[:30],
        "open_positions":state["positions"],"opened":opened,"closed":closed,
        "top_candidates":candidates[:20]
    }
    SNAPSHOT_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"opened":opened,"closed":closed,"top":candidates[:5],
                      "open_positions":len(state["positions"])},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
