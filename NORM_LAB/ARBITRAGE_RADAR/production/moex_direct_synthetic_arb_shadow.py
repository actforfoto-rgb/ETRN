from __future__ import annotations

import csv, json, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT.parent/"results"
STATE_DIR=ROOT/"event_state";STATE_DIR.mkdir(parents=True,exist_ok=True)

ROUTES=RESULTS/"moex_calendar_event_universe_walkforward.json"
LAST=STATE_DIR/"moex_direct_synthetic_last.json"
TS=STATE_DIR/"moex_direct_synthetic_timeseries.csv"
CAND=STATE_DIR/"moex_direct_synthetic_candidates.csv"

ISS="https://iss.moex.com/iss"
PAGE="https://www.moex.com/ru/spreads"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-DIRECT-SYNTHETIC-ARB/1.0"})

SAMPLES=20
SLEEP_SEC=3
MIN_TRADES=20
EXTRA_BUFFER_RUB=2.0
MAX_REQUEST_GAP_MS=1500
MIN_NET_RUB=1.0
CONFIRM_SAMPLES=3

TS_FIELDS=["utc","sample","spread","near","far","direct_bid","direct_ask",
           "near_bid","near_ask","far_bid","far_ask","synthetic_buy","synthetic_sell",
           "cheap_direct_gross_rub","rich_direct_gross_rub","roundtrip_fee_rub",
           "net_buy_direct_rub","net_sell_direct_rub","request_gap_ms","trades","volume","confirmed"]
CAND_FIELDS=["utc","spread","direction","net_rub","confirmed_samples","request_gap_ms"]

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

def page_snapshot():
    t0=int(time.time()*1000)
    r=S.get(PAGE,timeout=15);r.raise_for_status()
    t1=int(time.time()*1000)
    soup=BeautifulSoup(r.text,"html.parser");out={}
    for tr in soup.find_all("tr"):
        c=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if len(c)<9:continue
        code=c[0].strip()
        if "-" not in code:continue
        out[code]={"bid":parse_num(c[2]),"ask":parse_num(c[3]),
                   "trades":parse_num(c[8]) or 0,"volume":parse_num(c[6]) or 0}
    return out,(t0+t1)//2,t1-t0

def iss_snapshot():
    t0=int(time.time()*1000)
    r=S.get(f"{ISS}/engines/futures/markets/forts/boards/RFUD/securities.json",
            params={"iss.meta":"off","iss.only":"securities,marketdata"},timeout=15)
    r.raise_for_status();j=r.json();t1=int(time.time()*1000)
    def rows(name):
        b=j.get(name,{})
        cols=b.get("columns",[])
        return [dict(zip(cols,x)) for x in b.get("data",[])]
    sec={x.get("SECID"):x for x in rows("securities")}
    md={x.get("SECID"):x for x in rows("marketdata")}
    return sec,md,(t0+t1)//2,t1-t0

def append(path,fields,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in fields})

def unit(s):
    ms=num(s.get("MINSTEP")) or 1;sp=num(s.get("STEPPRICE")) or ms
    return sp/ms

def main():
    cfg=json.loads(ROUTES.read_text(encoding="utf-8"))
    route_map={r["spread"]:r for r in cfg.get("routes",[]) if r.get("near") and r.get("far")}
    confirmations={}
    samples=[];candidates=[]

    for sample in range(SAMPLES):
        with ThreadPoolExecutor(max_workers=2) as pool:
            fp=pool.submit(page_snapshot);fi=pool.submit(iss_snapshot)
            pg,pt,plat=fp.result()
            sec,md,it,ilat=fi.result()
        gap=abs(pt-it)

        for code,r in route_map.items():
            q=pg.get(code);ns=sec.get(r["near"]);fs=sec.get(r["far"])
            nm=md.get(r["near"]);fm=md.get(r["far"])
            if not q or not ns or not fs or not nm or not fm:continue
            if q["trades"]<MIN_TRADES:continue
            db,da=q["bid"],q["ask"]
            nb,na=num(nm.get("BID")),num(nm.get("OFFER"))
            fb,fa=num(fm.get("BID")),num(fm.get("OFFER"))
            if None in (db,da,nb,na,fb,fa):continue
            if da<db or na<nb or fa<fb:continue
            u=unit(ns)
            # MOEX convention: calendar spread = FAR - NEAR.
            synth_buy=fa-nb    # sell near at bid + buy far at ask
            synth_sell=fb-na   # buy near at ask + sell far at bid

            # BUY direct at ask, offset by SELL synthetic.
            gross_buy=(synth_sell-da)*u
            # SELL direct at bid, offset by BUY synthetic.
            gross_sell=(db-synth_buy)*u

            # Conservative fees: no calendar-spread discount assumed.
            nf=num(ns.get("BUYSELLFEE")) or 0
            ff=num(fs.get("BUYSELLFEE")) or 0
            direct_fee=nf+ff
            synthetic_fee=nf+ff
            rt=direct_fee+synthetic_fee+EXTRA_BUFFER_RUB
            net_buy=gross_buy-rt
            net_sell=gross_sell-rt

            direction="BUY_DIRECT_SELL_SYNTH" if net_buy>=net_sell else "SELL_DIRECT_BUY_SYNTH"
            best=max(net_buy,net_sell)
            key=(code,direction)
            if best>=MIN_NET_RUB and gap<=MAX_REQUEST_GAP_MS:
                confirmations[key]=confirmations.get(key,0)+1
            else:
                confirmations[key]=0
            confirmed=confirmations[key]>=CONFIRM_SAMPLES

            row={"utc":utc(),"sample":sample,"spread":code,"near":r["near"],"far":r["far"],
                 "direct_bid":db,"direct_ask":da,"near_bid":nb,"near_ask":na,
                 "far_bid":fb,"far_ask":fa,"synthetic_buy":synth_buy,"synthetic_sell":synth_sell,
                 "cheap_direct_gross_rub":gross_buy,"rich_direct_gross_rub":gross_sell,
                 "roundtrip_fee_rub":rt,"net_buy_direct_rub":net_buy,
                 "net_sell_direct_rub":net_sell,"request_gap_ms":gap,
                 "trades":q["trades"],"volume":q["volume"],"confirmed":confirmed}
            samples.append(row);append(TS,TS_FIELDS,row)
            if confirmed:
                cr={"utc":utc(),"spread":code,"direction":direction,"net_rub":best,
                    "confirmed_samples":confirmations[key],"request_gap_ms":gap}
                candidates.append(cr);append(CAND,CAND_FIELDS,cr)

        if sample<SAMPLES-1:time.sleep(SLEEP_SEC)

    report={"utc":utc(),"samples":len(samples),"positive_confirmed":len(candidates),
            "top":sorted(samples,key=lambda x:max(x["net_buy_direct_rub"],x["net_sell_direct_rub"]),reverse=True)[:30],
            "confirmed_candidates":candidates[-30:],
            "rules":{"min_trades":MIN_TRADES,"extra_buffer_rub":EXTRA_BUFFER_RUB,
                     "max_request_gap_ms":MAX_REQUEST_GAP_MS,
                     "confirm_samples":CONFIRM_SAMPLES,
                     "fee_assumption":"full near+far fee charged on direct spread plus full near+far fee on synthetic offset; no CS discount assumed"},
            "note":"Diagnostic shadow only. Page spread quotes and ISS leg quotes are separate public feeds; broker/API synchronized feed is required before PAPER."}
    LAST.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"samples":len(samples),"confirmed":len(candidates),
                      "top":report["top"][:10]},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
