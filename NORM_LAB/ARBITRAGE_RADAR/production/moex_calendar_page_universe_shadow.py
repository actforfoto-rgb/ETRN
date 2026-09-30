from __future__ import annotations

import csv, json, math, statistics, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT.parent/"results"
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)

CFG_FILE=RESULTS/"moex_calendar_event_universe_walkforward.json"
STATE_FILE=STATE_DIR/"moex_calendar_page_universe_state.json"
LAST_FILE=STATE_DIR/"moex_calendar_page_universe_last.json"
TS_FILE=STATE_DIR/"moex_calendar_page_universe_timeseries.csv"
LEDGER_FILE=STATE_DIR/"moex_calendar_page_universe_ledger.csv"

ISS="https://iss.moex.com/iss"
PAGE="https://www.moex.com/ru/derivatives/spreads/calendar-spreads.aspx"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-MOEX-CALENDAR-PAGE-SHADOW/1.0"})
MSK=ZoneInfo("Europe/Moscow")

LOOKBACK=60
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_MIN=180
EXTRA_COST_STRESS_RUB=5.0
MIN_EXPECTED_NET_RUB=5.0
MIN_HOLDOUT_EVENTS=4
MIN_HOLDOUT_POSITIVE_PCT=70.0
MIN_CURRENT_TRADES=50
MAX_ROUTES=12
QUOTE_STALE_MIN=20

LEDGER_FIELDS=[
 "utc","event","spread","near","far","direction","entry_z","exit_z",
 "entry_price","exit_price","expected_net_rub","realized_net_rub",
 "hold_min","reason"
]
TS_FIELDS=[
 "utc","spread","near","far","bid","ask","mid","last","trades","volume_contracts",
 "baseline_mean","baseline_sd","z","selected_entry_z","unit_rub",
 "cost_rub","expected_sell_rub","expected_buy_rub","quote_age_min",
 "position"
]

def utc(): return datetime.now(timezone.utc).isoformat()

def num(v):
    try:return float(v)
    except:return None

def parse_num(s):
    if s is None:return None
    x=str(s).replace("\xa0","").replace(" ","").replace(",",".").strip()
    if x in ("","-","—"):return None
    try:return float(x)
    except:return None

def get_json(url,params=None):
    r=S.get(url,params=params or {},timeout=25)
    r.raise_for_status()
    return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def load_cfg():
    raw=json.loads(CFG_FILE.read_text(encoding="utf-8"))
    routes=[]
    for r in raw.get("routes",[]):
        h=r.get("holdout") or {}
        if not r.get("holdout_pass"): continue
        if int(h.get("n") or 0)<MIN_HOLDOUT_EVENTS: continue
        if float(h.get("positive_pct") or 0)<MIN_HOLDOUT_POSITIVE_PCT: continue
        if float(r.get("current_trades") or 0)<MIN_CURRENT_TRADES: continue
        if float(h.get("median_net_rub") or 0)<=0: continue
        score=(float(h.get("median_net_rub") or 0)
               * math.sqrt(max(1,int(h.get("n") or 1)))
               * min(1.0,float(h.get("positive_pct") or 0)/100))
        routes.append({**r,"shadow_score":score})
    routes.sort(key=lambda r:(r["shadow_score"],r.get("current_trades",0)),reverse=True)
    return routes[:MAX_ROUTES]

def page_rows():
    r=S.get(PAGE,timeout=25);r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    out={}
    for tr in soup.find_all("tr"):
        c=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if len(c)<9:continue
        code=c[0].strip()
        if not code or "-" not in code:continue
        out[code]={
          "code":code,"last":parse_num(c[1]),"bid":parse_num(c[2]),"ask":parse_num(c[3]),
          "high":parse_num(c[4]),"low":parse_num(c[5]),
          "volume_contracts":parse_num(c[6]) or 0.0,
          "volume_rub":parse_num(c[7]) or 0.0,
          "trades":parse_num(c[8]) or 0.0
        }
    return out

def fut_meta(secid):
    j=get_json(f"{ISS}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",
               {"iss.meta":"off"})
    s=rows(j,"securities")
    if not s:return None
    a=s[0]
    ms=num(a.get("MINSTEP")) or 1.0
    sp=num(a.get("STEPPRICE")) or ms
    return {"unit":sp/ms,"fee":num(a.get("BUYSELLFEE")) or 0.0}

def candles(secid):
    till=datetime.now(MSK)
    frm=till-timedelta(days=3)
    out=[];start=0
    while True:
        j=get_json(f"{ISS}/engines/futures/markets/forts/securities/{secid}/candles.json",
                   {"from":frm.strftime("%Y-%m-%d"),"till":till.strftime("%Y-%m-%d"),
                    "interval":1,"start":start,"iss.meta":"off"})
        rr=rows(j,"candles")
        if not rr:break
        out.extend(rr)
        if len(rr)<500:break
        start+=len(rr)
        if start>5000:break
    return {x.get("begin"):{"close":num(x.get("close")),"volume":num(x.get("volume")) or 0.0}
            for x in out if x.get("begin") and num(x.get("close")) is not None}

def baseline(near,far):
    A=candles(near);B=candles(far)
    ts=[t for t in sorted(set(A)&set(B))
        if A[t]["volume"]>0 and B[t]["volume"]>0][-LOOKBACK:]
    xs=[B[t]["close"]-A[t]["close"] for t in ts]
    if len(xs)<30:return None
    sd=statistics.pstdev(xs)
    if sd<=1e-12:return None
    return {"mean":statistics.fmean(xs),"sd":sd,"n":len(xs)}

def load_state():
    if STATE_FILE.exists():
        try:return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:pass
    return {"quotes":{},"positions":{}}

def save_state(st):
    STATE_FILE.write_text(json.dumps(st,ensure_ascii=False,indent=2),encoding="utf-8")

def append_csv(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def quote_age(st,code,pr,now):
    sig=f"{pr.get('bid')}|{pr.get('ask')}|{pr.get('last')}|{pr.get('trades')}|{pr.get('volume_contracts')}"
    q=st["quotes"].get(code)
    if not q or q.get("sig")!=sig:
        st["quotes"][code]={"sig":sig,"changed_ts":now}
        return 0.0
    return max(0.0,(now-float(q.get("changed_ts") or now))/60)

def main():
    cfg=load_cfg()
    page=page_rows()
    st=load_state()
    now=time.time()
    samples=[]
    summary=[]

    for r in cfg:
        code=r["spread"]
        pr=page.get(code)
        if not pr or pr["bid"] is None or pr["ask"] is None or pr["bid"]==0 or pr["ask"]==0:
            summary.append({"spread":code,"status":"NO_TWO_SIDED_PAGE_QUOTE"})
            continue
        bl=baseline(r["near"],r["far"])
        nm=fut_meta(r["near"])
        if not bl or not nm:
            summary.append({"spread":code,"status":"NO_BASELINE_OR_META"})
            continue

        age=quote_age(st,code,pr,now)
        unit=float(nm["unit"])
        mid=(pr["bid"]+pr["ask"])/2
        z=(mid-bl["mean"])/bl["sd"]
        cost=float(r.get("cost_proxy_rub") or 0)+EXTRA_COST_STRESS_RUB
        exp_sell=(pr["bid"]-bl["mean"])*unit-cost
        exp_buy=(bl["mean"]-pr["ask"])*unit-cost
        pos=st["positions"].get(code)
        action=""

        if pos:
            hold=(now-float(pos["opened_ts"]))/60
            if pos["direction"]=="SELL":
                pnl=(float(pos["entry_price"])-pr["ask"])*unit-float(pos["cost_rub"])
                exit_px=pr["ask"]
            else:
                pnl=(pr["bid"]-float(pos["entry_price"]))*unit-float(pos["cost_rub"])
                exit_px=pr["bid"]
            reason=None
            if abs(z)<=EXIT_Z:reason="CONVERGENCE"
            elif abs(z)>=STOP_Z:reason="Z_STOP"
            elif hold>=MAX_HOLD_MIN:reason="MAX_HOLD"
            if reason:
                append_csv(LEDGER_FILE,LEDGER_FIELDS,{
                  "utc":utc(),"event":"CLOSE","spread":code,"near":r["near"],"far":r["far"],
                  "direction":pos["direction"],"entry_z":pos["entry_z"],"exit_z":z,
                  "entry_price":pos["entry_price"],"exit_price":exit_px,
                  "expected_net_rub":pos["expected_net_rub"],"realized_net_rub":pnl,
                  "hold_min":hold,"reason":reason
                })
                del st["positions"][code]
                pos=None
                action=f"CLOSE_{reason}"

        if not pos and age<=QUOTE_STALE_MIN:
            ez=float(r["selected_entry_z"])
            direction=None;entry_px=None;expected=None
            if z>=ez and exp_sell>=MIN_EXPECTED_NET_RUB:
                direction="SELL";entry_px=pr["bid"];expected=exp_sell
            elif z<=-ez and exp_buy>=MIN_EXPECTED_NET_RUB:
                direction="BUY";entry_px=pr["ask"];expected=exp_buy
            if direction:
                st["positions"][code]={
                  "direction":direction,"opened_ts":now,"entry_z":z,
                  "entry_price":entry_px,"expected_net_rub":expected,"cost_rub":cost,
                  "near":r["near"],"far":r["far"]
                }
                append_csv(LEDGER_FILE,LEDGER_FIELDS,{
                  "utc":utc(),"event":"OPEN","spread":code,"near":r["near"],"far":r["far"],
                  "direction":direction,"entry_z":z,"entry_price":entry_px,
                  "expected_net_rub":expected,"reason":"DIRECT_PAGE_DISLOCATION"
                })
                action=f"OPEN_{direction}"

        row={
          "utc":utc(),"spread":code,"near":r["near"],"far":r["far"],
          "bid":pr["bid"],"ask":pr["ask"],"mid":mid,"last":pr["last"],
          "trades":pr["trades"],"volume_contracts":pr["volume_contracts"],
          "baseline_mean":bl["mean"],"baseline_sd":bl["sd"],"z":z,
          "selected_entry_z":r["selected_entry_z"],"unit_rub":unit,
          "cost_rub":cost,"expected_sell_rub":exp_sell,"expected_buy_rub":exp_buy,
          "quote_age_min":age,"position":(st["positions"].get(code) or {}).get("direction","FLAT")
        }
        append_csv(TS_FILE,TS_FIELDS,row)
        samples.append(row)
        summary.append({"spread":code,"status":"OK","z":z,
                        "expected_best_rub":max(exp_sell,exp_buy),
                        "quote_age_min":age,"action":action,
                        "position":row["position"]})

    save_state(st)
    report={"utc":utc(),"mode":"DIRECT_PAGE_QUOTE_SHADOW_ONLY",
            "selected_routes":len(cfg),"samples":samples,
            "summary":sorted(summary,key=lambda x:abs(float(x.get("z") or 0)),reverse=True),
            "open_positions":st["positions"],
            "caveat":"MOEX web-page quote has no guaranteed broker-feed timestamp; PAPER requires broker/API executable feed."}
    LAST_FILE.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"selected_routes":len(cfg),"open_positions":len(st["positions"]),
                      "top":report["summary"][:12]},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
