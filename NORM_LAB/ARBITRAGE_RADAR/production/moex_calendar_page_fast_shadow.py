from __future__ import annotations

import csv, json, math, statistics, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT.parent/"results"
STATE_DIR=ROOT/"event_state";STATE_DIR.mkdir(parents=True,exist_ok=True)

CFG=RESULTS/"moex_calendar_event_universe_walkforward.json"
STATE_FILE=STATE_DIR/"moex_calendar_page_fast_state.json"
LAST_FILE=STATE_DIR/"moex_calendar_page_fast_last.json"
TS_FILE=STATE_DIR/"moex_calendar_page_fast_timeseries.csv"
LEDGER_FILE=STATE_DIR/"moex_calendar_page_fast_ledger.csv"

ISS="https://iss.moex.com/iss"
PAGE="https://www.moex.com/ru/derivatives/spreads/calendar-spreads.aspx"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-CALENDAR-FAST/1.0"})
MSK=ZoneInfo("Europe/Moscow")

MAX_ROUTES=10
MIN_HOLDOUT_N=4
MIN_HOLDOUT_POSITIVE=70
MIN_CURRENT_TRADES=50
LOOKBACK=60
SAMPLES=12
SLEEP_SEC=10
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_SEC=3*3600
EXTRA_COST_STRESS_RUB=5.0
MIN_EXPECTED_NET_RUB=5.0
MAX_STALE_SEC=180

LEDGER_FIELDS=["utc","event","spread","direction","entry_z","exit_z","entry_price","exit_price",
               "expected_net_rub","realized_net_rub","hold_sec","reason"]
TS_FIELDS=["utc","sample","spread","bid","ask","mid","trades","volume_contracts",
           "baseline_mean","baseline_sd","z","entry_z","cost_rub",
           "expected_sell_rub","expected_buy_rub","quote_stale_sec","phase","event"]

def utc():return datetime.now(timezone.utc).isoformat()

def num(v):
    try:return float(v)
    except:return None

def parse_num(x):
    if x is None:return None
    x=str(x).replace("\xa0","").replace(" ","").replace(",",".").strip()
    if x in ("","-","—"):return None
    try:return float(x)
    except:return None

def jget(url,params=None):
    r=S.get(url,params=params or {},timeout=20);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def page():
    r=S.get(PAGE,timeout=20);r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser");out={}
    for tr in soup.find_all("tr"):
        c=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if len(c)<9:continue
        code=c[0].strip()
        if not code or "-" not in code:continue
        out[code]={"bid":parse_num(c[2]),"ask":parse_num(c[3]),"last":parse_num(c[1]),
                   "volume":parse_num(c[6]) or 0,"trades":parse_num(c[8]) or 0}
    return out

def candles(secid):
    till=datetime.now(MSK);frm=till-timedelta(days=3)
    out=[];start=0
    while True:
        j=jget(f"{ISS}/engines/futures/markets/forts/securities/{secid}/candles.json",
               {"from":frm.strftime("%Y-%m-%d"),"till":till.strftime("%Y-%m-%d"),
                "interval":1,"start":start,"iss.meta":"off"})
        rr=rows(j,"candles")
        if not rr:break
        out.extend(rr)
        if len(rr)<500:break
        start+=len(rr)
        if start>5000:break
    return {r.get("begin"):{"close":num(r.get("close")),"volume":num(r.get("volume")) or 0}
            for r in out if r.get("begin") and num(r.get("close")) is not None}

def fut_unit(secid):
    j=jget(f"{ISS}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities")
    if not s:return None
    a=s[0];ms=num(a.get("MINSTEP")) or 1;sp=num(a.get("STEPPRICE")) or ms
    return sp/ms

def prep():
    raw=json.loads(CFG.read_text(encoding="utf-8"))
    cand=[]
    for r in raw.get("routes",[]):
        h=r.get("holdout") or {}
        if not r.get("holdout_pass"):continue
        if int(h.get("n") or 0)<MIN_HOLDOUT_N:continue
        if float(h.get("positive_pct") or 0)<MIN_HOLDOUT_POSITIVE:continue
        if float(r.get("current_trades") or 0)<MIN_CURRENT_TRADES:continue
        if float(h.get("median_net_rub") or 0)<=0:continue
        score=float(h["median_net_rub"])*math.sqrt(int(h["n"]))*float(h["positive_pct"])/100
        cand.append((score,r))
    cand.sort(key=lambda x:x[0],reverse=True)
    out=[]
    cache={}
    for _,r in cand[:MAX_ROUTES]:
        for secid in (r["near"],r["far"]):
            if secid not in cache:cache[secid]=candles(secid)
        A=cache[r["near"]];B=cache[r["far"]]
        ts=[t for t in sorted(set(A)&set(B)) if A[t]["volume"]>0 and B[t]["volume"]>0][-LOOKBACK:]
        xs=[B[t]["close"]-A[t]["close"] for t in ts]
        if len(xs)<30:continue
        sd=statistics.pstdev(xs)
        if sd<=1e-12:continue
        u=fut_unit(r["near"])
        if not u:continue
        out.append({"spread":r["spread"],"near":r["near"],"far":r["far"],
                    "entry_z":float(r["selected_entry_z"]),"mean":statistics.fmean(xs),"sd":sd,
                    "unit":u,"cost":float(r.get("cost_proxy_rub") or 0)+EXTRA_COST_STRESS_RUB,
                    "holdout":h})
    return out

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"routes":{}}

def save_state(st):STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def main():
    routes=prep();st=load_state();samples=[]
    for r in routes:
        st["routes"].setdefault(r["spread"],{"phase":"FLAT","position":None,"last_sig":None,"last_change_ts":0})

    for sample_no in range(SAMPLES):
        now=time.time()
        try:pg=page()
        except Exception as e:
            samples.append({"utc":utc(),"sample":sample_no,"error":f"{type(e).__name__}: {e}"})
            if sample_no<SAMPLES-1:time.sleep(SLEEP_SEC)
            continue

        for r in routes:
            q=pg.get(r["spread"]);rs=st["routes"][r["spread"]]
            if not q or q["bid"] is None or q["ask"] is None or q["bid"]<=0 or q["ask"]<=0:continue
            sig=f"{q['bid']}|{q['ask']}|{q['last']}|{q['trades']}|{q['volume']}"
            if sig!=rs.get("last_sig"):
                rs["last_sig"]=sig;rs["last_change_ts"]=now
            stale=now-float(rs.get("last_change_ts") or now)
            mid=(q["bid"]+q["ask"])/2
            z=(mid-r["mean"])/r["sd"]
            exp_sell=(q["bid"]-r["mean"])*r["unit"]-r["cost"]
            exp_buy=(r["mean"]-q["ask"])*r["unit"]-r["cost"]
            event=""
            pos=rs.get("position")

            if pos:
                hold=now-pos["opened_ts"]
                if pos["direction"]=="SELL":
                    pnl=(pos["entry_price"]-q["ask"])*r["unit"]-r["cost"];exit_px=q["ask"]
                else:
                    pnl=(q["bid"]-pos["entry_price"])*r["unit"]-r["cost"];exit_px=q["bid"]
                reason=None
                if abs(z)<=EXIT_Z:reason="CONVERGENCE"
                elif abs(z)>=STOP_Z:reason="Z_STOP"
                elif hold>=MAX_HOLD_SEC:reason="MAX_HOLD"
                elif stale>MAX_STALE_SEC:reason="STALE_QUOTE"
                if reason:
                    event="CLOSE_"+reason
                    append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":"CLOSE","spread":r["spread"],
                      "direction":pos["direction"],"entry_z":pos["entry_z"],"exit_z":z,
                      "entry_price":pos["entry_price"],"exit_price":exit_px,
                      "expected_net_rub":pos["expected_net_rub"],"realized_net_rub":pnl,
                      "hold_sec":hold,"reason":reason})
                    rs["position"]=None;rs["phase"]="FLAT";pos=None

            if not pos and stale<=MAX_STALE_SEC:
                direction=None
                if z>=r["entry_z"] and exp_sell>=MIN_EXPECTED_NET_RUB:
                    direction="SELL";entry=q["bid"];expected=exp_sell
                elif z<=-r["entry_z"] and exp_buy>=MIN_EXPECTED_NET_RUB:
                    direction="BUY";entry=q["ask"];expected=exp_buy
                if direction:
                    rs["position"]={"direction":direction,"opened_ts":now,"entry_z":z,
                                    "entry_price":entry,"expected_net_rub":expected}
                    rs["phase"]="HEDGED_SPREAD";event="OPEN_"+direction
                    append(LEDGER_FILE,LEDGER_FIELDS,{"utc":utc(),"event":"OPEN","spread":r["spread"],
                      "direction":direction,"entry_z":z,"entry_price":entry,
                      "expected_net_rub":expected,"reason":"DIRECT_SPREAD_EVENT"})

            row={"utc":utc(),"sample":sample_no,"spread":r["spread"],"bid":q["bid"],"ask":q["ask"],
                 "mid":mid,"trades":q["trades"],"volume_contracts":q["volume"],
                 "baseline_mean":r["mean"],"baseline_sd":r["sd"],"z":z,"entry_z":r["entry_z"],
                 "cost_rub":r["cost"],"expected_sell_rub":exp_sell,"expected_buy_rub":exp_buy,
                 "quote_stale_sec":stale,"phase":rs["phase"],"event":event}
            samples.append(row);append(TS_FILE,TS_FIELDS,row)
        save_state(st)
        if sample_no<SAMPLES-1:time.sleep(SLEEP_SEC)

    report={"utc":utc(),"routes":routes,"samples":samples[-120:],
            "state":st,"mode":"DIRECT_PAGE_FAST_SHADOW_ONLY",
            "caveat":"Web-page quote is diagnostic. Broker/API executable feed is mandatory before PAPER."}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"routes":len(routes),
                      "phases":{k:v["phase"] for k,v in st["routes"].items()},
                      "last":samples[-10:]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
