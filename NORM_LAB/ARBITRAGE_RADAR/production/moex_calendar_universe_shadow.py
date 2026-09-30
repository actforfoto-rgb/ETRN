from __future__ import annotations

import csv, json, re, time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
LAST=STATE_DIR/"moex_calendar_universe_last.json"
TS=STATE_DIR/"moex_calendar_universe_timeseries.csv"
ALERTS=STATE_DIR/"moex_calendar_universe_alerts.csv"

PAGE="https://www.moex.com/ru/derivatives/spreads/calendar-spreads.aspx"
ISS="https://iss.moex.com/iss"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-MOEX-CALENDAR-UNIVERSE/1.0"})

MIN_TRADES=1
MIN_VOLUME=1
EXTRA_BUFFER_RUB=2.0
DISPLAY_RE=re.compile(r"^(.+)-(\\d{1,2}\\.\\d{2})-(\\d{1,2}\\.\\d{2})$")

TS_FIELDS=[
 "utc","spread","near_secid","far_secid","atomic_bid","atomic_ask",
 "synthetic_bid","synthetic_ask","atomic_mid","synthetic_mid",
 "sell_atomic_buy_synth_rub","sell_synth_buy_atomic_rub",
 "fee_buffer_rub","atomic_trades","atomic_volume","status"
]

def utc():
    return datetime.now(timezone.utc).isoformat()

def num(v):
    try:return float(v)
    except:return None

def parse_num(s):
    if s is None:return None
    x=str(s).replace(" ","").replace(" ","").replace(",",".").strip()
    if x in ("","-","—"):return None
    try:return float(x)
    except:return None

def get_json(url,params=None):
    r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def load_page_rows():
    r=S.get(PAGE,timeout=30);r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    out=[]
    for tr in soup.find_all("tr"):
        cells=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if len(cells)<9:continue
        code=cells[0].strip()
        if not DISPLAY_RE.match(code):continue
        out.append({
          "code":code,
          "last":parse_num(cells[1]),
          "bid":parse_num(cells[2]),
          "ask":parse_num(cells[3]),
          "high":parse_num(cells[4]),
          "low":parse_num(cells[5]),
          "volume_contracts":parse_num(cells[6]),
          "volume_rub":parse_num(cells[7]),
          "trades":parse_num(cells[8]),
        })
    return out

def futures_snapshot():
    j=get_json(f"{ISS}/engines/futures/markets/forts/boards/RFUD/securities.json",
               {"iss.meta":"off","iss.only":"securities,marketdata"})
    secs=rows(j,"securities")
    md={r.get("SECID"):r for r in rows(j,"marketdata")}
    by_short={}
    for s in secs:
        sh=(s.get("SHORTNAME") or "").strip()
        if sh:by_short[sh]=s
    return by_short,md

def unit(sec):
    ms=num(sec.get("MINSTEP")) or 1.0
    sp=num(sec.get("STEPPRICE")) or ms
    return sp/ms

def append(path,row):
    new=not path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=TS_FIELDS)
        if new:w.writeheader()
        w.writerow({k:row.get(k,"") for k in TS_FIELDS})

def resolve_legs(display,by_short):
    m=DISPLAY_RE.match(display)
    if not m:return None
    root,m1,m2=m.groups()
    near_name=f"{root}-{m1}"
    far_name=f"{root}-{m2}"
    n=by_short.get(near_name)
    f=by_short.get(far_name)
    if not n or not f:return None
    return n,f

def main():
    page_rows=load_page_rows()
    by_short,md=futures_snapshot()
    report=[]

    for pr in page_rows:
        if pr["bid"] is None or pr["ask"] is None:continue
        if pr["bid"]==0 and pr["ask"]==0:continue
        legs=resolve_legs(pr["code"],by_short)
        if not legs:continue
        ns,fs=legs
        nid,fid=ns["SECID"],fs["SECID"]
        nm,fm=md.get(nid,{}),md.get(fid,{})
        nb,na=num(nm.get("BID")),num(nm.get("OFFER"))
        fb,fa=num(fm.get("BID")),num(fm.get("OFFER"))
        if None in (nb,na,fb,fa):continue

        nu,fu=unit(ns),unit(fs)
        if nu<=0 or fu<=0:continue
        fqty=nu/fu

        synth_bid=fb-na
        synth_ask=fa-nb
        atomic_mid=(pr["bid"]+pr["ask"])/2
        synth_mid=(synth_bid+synth_ask)/2

        # Conservative full roundtrip-equivalent cost proxy:
        # both legs exchange scalper fees + extra execution buffer.
        nsc=num(ns.get("SCALPERFEE")) or 0.0
        fsc=num(fs.get("SCALPERFEE")) or 0.0
        fee_buf=2*(nsc+fqty*fsc)+EXTRA_BUFFER_RUB

        # Same economics, opposite implementations:
        # A) sell atomic, buy synthetic
        edge_a=(pr["bid"]-synth_ask)*nu-fee_buf
        # B) sell synthetic, buy atomic
        edge_b=(synth_bid-pr["ask"])*nu-fee_buf
        best=max(edge_a,edge_b)
        # Page quote and ISS leg quote are cross-source and may not be time-aligned.
        # Positive arithmetic is discovery evidence only, never executable evidence.
        status="CROSS_SOURCE_POSITIVE_DIAGNOSTIC" if best>0 else "NO_LOCK"

        row={
          "utc":utc(),"spread":pr["code"],"near_secid":nid,"far_secid":fid,
          "atomic_bid":pr["bid"],"atomic_ask":pr["ask"],
          "synthetic_bid":synth_bid,"synthetic_ask":synth_ask,
          "atomic_mid":atomic_mid,"synthetic_mid":synth_mid,
          "sell_atomic_buy_synth_rub":edge_a,
          "sell_synth_buy_atomic_rub":edge_b,
          "fee_buffer_rub":fee_buf,
          "atomic_trades":pr["trades"],"atomic_volume":pr["volume_contracts"],
          "status":status
        }
        report.append(row)
        append(TS,row)
        # Do not promote or alert as executable until a single synchronized broker/
        # exchange feed provides both calendar-spread and leg books.
        if False:
            append(ALERTS,row)

    report.sort(key=lambda r:max(r["sell_atomic_buy_synth_rub"],r["sell_synth_buy_atomic_rub"]),reverse=True)
    summary={
      "utc":utc(),
      "page_spreads":len(page_rows),
      "resolved_live_spreads":len(report),
      "positive_cross_source_diagnostics":sum(r["status"]=="CROSS_SOURCE_POSITIVE_DIAGNOSTIC" for r in report),
      "positive_lock_candidates":0,
      "top":report[:100]
    }
    LAST.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"page_spreads":len(page_rows),"resolved":len(report),
                      "positive":summary["positive_lock_candidates"],
                      "top":report[:20]},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
