from __future__ import annotations

import json, math, re, statistics
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

ISS="https://iss.moex.com/iss"
PAGE="https://www.moex.com/ru/derivatives/spreads/calendar-spreads.aspx"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-MOEX-CALENDAR-EVENT-UNIVERSE/1.0"})
MSK=ZoneInfo("Europe/Moscow")

LOOKBACK=60
ENTRY_ZS=[2.0,2.5,3.0]
EXIT_Z=0.5
STOP_Z=5.0
MAX_HOLD_BARS=120
HISTORY_DAYS=8
TOP_SPREADS=50
MIN_CURRENT_TRADES=2
MIN_CURRENT_VOLUME=4
MIN_TRAIN=5
TRAIN_FRAC=0.70
EXTRA_BUFFER_RUB=2.0

DISPLAY_RE=re.compile(r"^(.+)-(\d{1,2}\.\d{2})-(\d{1,2}\.\d{2})$")

def get(url,params=None,timeout=30):
    r=S.get(url,params=params or {},timeout=timeout)
    r.raise_for_status()
    return r

def jget(url,params=None):
    return get(url,params).json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def parse_num(s):
    if s is None:return None
    x=str(s).replace("\xa0","").replace(" ","").replace(",",".").strip()
    if x in ("","-","—"):return None
    try:return float(x)
    except:return None

def page_spreads():
    html=get(PAGE).text
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    for tr in soup.find_all("tr"):
        c=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if len(c)<9:continue
        code=c[0].strip()
        if not DISPLAY_RE.match(code):continue
        r={
          "code":code,"last":parse_num(c[1]),"bid":parse_num(c[2]),"ask":parse_num(c[3]),
          "volume_contracts":parse_num(c[6]) or 0.0,
          "volume_rub":parse_num(c[7]) or 0.0,
          "trades":parse_num(c[8]) or 0.0
        }
        if r["trades"]>=MIN_CURRENT_TRADES and r["volume_contracts"]>=MIN_CURRENT_VOLUME:
            out.append(r)
    out.sort(key=lambda r:(r["volume_contracts"],r["trades"],r["volume_rub"]),reverse=True)
    return out[:TOP_SPREADS]

def snapshot():
    j=jget(f"{ISS}/engines/futures/markets/forts/boards/RFUD/securities.json",
           {"iss.meta":"off","iss.only":"securities,marketdata"})
    secs=rows(j,"securities")
    md={r.get("SECID"):r for r in rows(j,"marketdata")}
    by_short={}
    for s in secs:
        sh=(s.get("SHORTNAME") or "").strip()
        if sh:by_short[sh]=s
    return by_short,md

def resolve(display,by_short):
    m=DISPLAY_RE.match(display)
    if not m:return None
    root,a,b=m.groups()
    n=by_short.get(f"{root}-{a}")
    f=by_short.get(f"{root}-{b}")
    if not n or not f:return None
    return n,f

def unit(s):
    ms=num(s.get("MINSTEP")) or 1.0
    sp=num(s.get("STEPPRICE")) or ms
    return sp/ms

def candles(secid,frm,till):
    url=f"{ISS}/engines/futures/markets/forts/securities/{secid}/candles.json"
    out=[];start=0
    while True:
        j=jget(url,{"interval":1,"from":frm,"till":till,"start":start,"iss.meta":"off"})
        rr=rows(j,"candles")
        if not rr:break
        out.extend(rr)
        if len(rr)<500:break
        start+=len(rr)
        if start>15000:break
    return {r.get("begin"):{"close":num(r.get("close")),"volume":num(r.get("volume")) or 0.0}
            for r in out if r.get("begin") and num(r.get("close")) is not None}

def current_cost(ns,fs,nm,fm):
    nu,fu=unit(ns),unit(fs)
    if nu<=0 or fu<=0:return None
    fqty=nu/fu
    nfee=num(ns.get("BUYSELLFEE")) or 0.0
    ffee=num(fs.get("BUYSELLFEE")) or 0.0
    fees=2*(nfee+fqty*ffee)
    nb,na=num(nm.get("BID")),num(nm.get("OFFER"))
    fb,fa=num(fm.get("BID")),num(fm.get("OFFER"))
    spread=0.0
    if None not in (nb,na):spread+=(na-nb)*nu
    if None not in (fb,fa):spread+=(fa-fb)*fqty*fu
    return {"unit":nu,"far_qty":fqty,"fees":fees,
            "exit_spread":spread,"total":fees+spread+EXTRA_BUFFER_RUB}

def simulate(A,B,cost,entry_z):
    ts=[t for t in sorted(set(A)&set(B))
        if A[t]["volume"]>0 and B[t]["volume"]>0]
    hist=deque(maxlen=LOOKBACK);pos=None;tr=[]
    for t in ts:
        x=B[t]["close"]-A[t]["close"]
        if len(hist)<LOOKBACK:
            hist.append(x);continue
        mean=statistics.fmean(hist);sd=statistics.pstdev(hist)
        z=(x-mean)/sd if sd>1e-12 else 0.0
        if pos:
            pos["bars"]+=1
            pnl=((pos["entry_x"]-x) if pos["dir"]=="HIGH" else (x-pos["entry_x"]))*cost["unit"]-cost["total"]
            reason=None
            if abs(z)<=EXIT_Z:reason="CONVERGENCE"
            elif abs(z)>=STOP_Z:reason="Z_STOP"
            elif pos["bars"]>=MAX_HOLD_BARS:reason="MAX_HOLD"
            if reason:
                tr.append({**pos,"exit_ts":t,"exit_x":x,"exit_z":z,
                           "net_rub":pnl,"reason":reason})
                pos=None
        else:
            if z>=entry_z:
                expected=(x-mean)*cost["unit"]-cost["total"]
                if expected>0:
                    pos={"entry_ts":t,"entry_x":x,"entry_z":z,"dir":"HIGH",
                         "expected_net_rub":expected,"bars":0}
            elif z<=-entry_z:
                expected=(mean-x)*cost["unit"]-cost["total"]
                if expected>0:
                    pos={"entry_ts":t,"entry_x":x,"entry_z":z,"dir":"LOW",
                         "expected_net_rub":expected,"bars":0}
        hist.append(x)
    return tr,len(ts)

def metrics(xs):
    if not xs:return None
    v=[x["net_rub"] for x in xs]
    return {"n":len(v),"positive_pct":100*sum(x>0 for x in v)/len(v),
            "median_net_rub":statistics.median(v),"mean_net_rub":statistics.fmean(v),
            "aggregate_net_rub":sum(v),"worst_net_rub":min(v),"best_net_rub":max(v)}

def main():
    candidates=page_spreads()
    by_short,md=snapshot()
    now=datetime.now(MSK)
    frm=(now-timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    till=now.strftime("%Y-%m-%d")
    cache={}
    results=[];errors=[]

    for p in candidates:
        try:
            legs=resolve(p["code"],by_short)
            if not legs:
                errors.append({"spread":p["code"],"error":"legs unresolved"})
                continue
            ns,fs=legs
            nid,fid=ns["SECID"],fs["SECID"]
            nm,fm=md.get(nid,{}),md.get(fid,{})
            cost=current_cost(ns,fs,nm,fm)
            if not cost:
                continue
            for sid in (nid,fid):
                if sid not in cache:cache[sid]=candles(sid,frm,till)
            A,B=cache[nid],cache[fid]

            threshold_rows=[]
            for ez in ENTRY_ZS:
                tr,bars=simulate(A,B,cost,ez)
                if not tr:continue
                times=sorted(set(int(datetime.fromisoformat(x["entry_ts"]).timestamp()) for x in tr))
                if len(times)<2:continue
                cut=times[max(0,min(len(times)-1,int(len(times)*TRAIN_FRAC)))]
                train=[x for x in tr if int(datetime.fromisoformat(x["entry_ts"]).timestamp())<cut]
                hold=[x for x in tr if int(datetime.fromisoformat(x["entry_ts"]).timestamp())>=cut]
                tm,hm=metrics(train),metrics(hold)
                if not tm or tm["n"]<MIN_TRAIN:continue
                score=tm["median_net_rub"]*math.sqrt(tm["n"])*(tm["positive_pct"]/100)
                threshold_rows.append({"entry_z":ez,"score":score,"train":tm,"holdout":hm,
                                       "bars":bars,"trades":tr,"cutoff_epoch":cut})
            if not threshold_rows:continue
            threshold_rows.sort(key=lambda x:x["score"],reverse=True)
            sel=threshold_rows[0];hm=sel["holdout"]
            pass_hold=bool(hm and hm["n"]>=2 and hm["positive_pct"]>=55
                           and hm["median_net_rub"]>0 and hm["aggregate_net_rub"]>0)
            results.append({
              "spread":p["code"],"near":nid,"far":fid,
              "current_trades":p["trades"],"current_volume":p["volume_contracts"],
              "cost_proxy_rub":cost["total"],"selected_entry_z":sel["entry_z"],
              "train_score":sel["score"],"train":sel["train"],"holdout":hm,
              "cutoff_epoch":sel["cutoff_epoch"],
              "holdout_pass":pass_hold,
              "_selected_trades":sel["trades"]
            })
        except Exception as e:
            errors.append({"spread":p["code"],"error":f"{type(e).__name__}: {e}"[:700]})

    selected_events=[]
    clean_results=[]
    for r in results:
        trades=r.pop("_selected_trades",[])
        cutoff=int(r.get("cutoff_epoch") or 0)
        for t in trades:
            ep=int(datetime.fromisoformat(t["entry_ts"]).timestamp())
            selected_events.append({
              "spread":r["spread"],"near":r["near"],"far":r["far"],
              "selected_entry_z":r["selected_entry_z"],
              "train_score":r["train_score"],"holdout_pass":r["holdout_pass"],
              "phase":"HOLDOUT" if ep>=cutoff else "TRAIN",
              **t
            })
        clean_results.append(r)
    results=clean_results
    results.sort(key=lambda r:(1 if r["holdout_pass"] else 0,
                               (r.get("holdout") or {}).get("aggregate_net_rub",-1e99),
                               r["current_volume"]),reverse=True)
    selected_events.sort(key=lambda x:x["entry_ts"])
    report={"utc":now.isoformat(),"discovery_candidates":len(candidates),
            "tested_routes":len(results),
            "holdout_pass":sum(r["holdout_pass"] for r in results),
            "routes":results,"errors":errors}
    (OUT/"moex_calendar_event_universe_walkforward.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"moex_calendar_event_selected_trades.json").write_text(
        json.dumps(selected_events,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"candidates":len(candidates),"tested":len(results),
                      "holdout_pass":report["holdout_pass"],"top":results[:30],
                      "errors":errors[:20]},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
