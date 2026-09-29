from __future__ import annotations

import csv, json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})

def utc(): return datetime.now(timezone.utc).isoformat()

def get(url, timeout=20):
    r=S.get(url,timeout=timeout)
    r.raise_for_status()
    return r.json()

def block(j,name):
    x=j.get(name,{})
    cols=x.get("columns",[])
    rows=x.get("data",[])
    return [dict(zip(cols,r)) for r in rows]

def first_nonempty(j,names):
    for n in names:
        rows=block(j,n)
        if rows:return n,rows
    return None,[]

def probe_endpoint(label,url):
    try:
        j=get(url)
        meta={"label":label,"ok":True,"url":url,"blocks":{}}
        for k,v in j.items():
            if isinstance(v,dict) and "columns" in v and "data" in v:
                meta["blocks"][k]={"columns":v.get("columns",[]),"rows":len(v.get("data",[])),
                                   "sample":v.get("data",[])[:2]}
        return meta,j
    except Exception as e:
        return {"label":label,"ok":False,"url":url,"error":f"{type(e).__name__}: {e}"[:700]},None

def field(row,*names):
    for n in names:
        v=row.get(n)
        if v not in (None,""):
            return v
    return None

def num(v):
    try:return float(v)
    except:return None

def current_snapshot():
    probes=[
      ("SBER_CURRENT","https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/SBER.json?iss.meta=off"),
      ("SBERF_CURRENT","https://iss.moex.com/iss/engines/futures/markets/forts/securities/SBERF.json?iss.meta=off"),
      ("SBRF1226_CURRENT","https://iss.moex.com/iss/engines/futures/markets/forts/securities/SBRF-12.26.json?iss.meta=off"),
      ("SBRF0327_CURRENT","https://iss.moex.com/iss/engines/futures/markets/forts/securities/SBRF-3.27.json?iss.meta=off"),
      ("SBRF0627_CURRENT","https://iss.moex.com/iss/engines/futures/markets/forts/securities/SBRF-6.27.json?iss.meta=off"),
      ("CAL1226_0327","https://iss.moex.com/iss/engines/futures/markets/forts/securities/SBRF-12.26-3.27.json?iss.meta=off"),
      ("SBER_HISTORY","https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json?from=2026-09-20&till=2026-09-29&iss.meta=off"),
      ("SBRF1226_HISTORY_RFUD","https://iss.moex.com/iss/history/engines/futures/markets/forts/boards/RFUD/securities/SBRF-12.26.json?from=2026-09-20&till=2026-09-29&iss.meta=off"),
      ("SBRF1226_HISTORY_NOBOARD","https://iss.moex.com/iss/history/engines/futures/markets/forts/securities/SBRF-12.26.json?from=2026-09-20&till=2026-09-29&iss.meta=off"),
      ("CAL_HISTORY_RFUD","https://iss.moex.com/iss/history/engines/futures/markets/forts/boards/RFUD/securities/SBRF-12.26-3.27.json?from=2026-03-01&till=2026-09-29&iss.meta=off"),
    ]
    report=[]
    raw={}
    for label,url in probes:
        m,j=probe_endpoint(label,url);report.append(m)
        if j is not None:raw[label]=j
    (OUT/"moex_m01_probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return raw

def select_market_row(j):
    _,rows=first_nonempty(j,["marketdata","marketdata_yields","securities"])
    if not rows:return {}
    # Prefer marketdata with any bid/ask/last.
    for r in rows:
        if any(r.get(k) not in (None,"") for k in ["BID","OFFER","LAST","LASTTOPREVPRICE","SETTLEPRICE","SETTLEPRICE_CLR"]):
            return r
    return rows[0]

def select_security_row(j):
    rows=block(j,"securities")
    return rows[0] if rows else {}

def px(row,side):
    if side=="ask":
        return num(field(row,"OFFER","OFFERPRICE","BESTASK","ASK"))
    if side=="bid":
        return num(field(row,"BID","BIDPRICE","BESTBID"))
    return num(field(row,"LAST","LCURRENTPRICE","SETTLEPRICE_CLR","SETTLEPRICE","CLOSEPRICE","LEGALCLOSEPRICE"))

def build_current(raw):
    names=["SBER_CURRENT","SBERF_CURRENT","SBRF1226_CURRENT","SBRF0327_CURRENT","SBRF0627_CURRENT","CAL1226_0327"]
    rows=[]
    for n in names:
        j=raw.get(n)
        if not j:continue
        m=select_market_row(j); sec=select_security_row(j)
        rows.append({
            "utc":utc(),"instrument":n.replace("_CURRENT",""),
            "secid":field(sec,"SECID","SHORTNAME","SECNAME"),
            "bid":px(m,"bid"),"ask":px(m,"ask"),"last":px(m,"last"),
            "settle":num(field(m,"SETTLEPRICE_CLR","SETTLEPRICE")),
            "open_interest":num(field(m,"OPENPOSITION","OPENINTEREST")),
            "volume":num(field(m,"VOLTODAY","VOLUME")),
            "numtrades":num(field(m,"NUMTRADES","NUMTRADES_TODAY")),
            "lasttrade":field(m,"LASTTRADETIME","SYSTIME","UPDATETIME"),
            "expiry":field(sec,"LASTTRADEDATE","EXPIRATIONDATE","LASTDELDATE"),
            "lot_size":field(sec,"LOTSIZE","LOTVOLUME","MINSTEP"),
            "step_price":field(sec,"STEPPRICE","STEPPRICET","STEPPRICECL"),
        })
    p=OUT/"moex_m01_current.csv"
    if rows:
        with p.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)

    # Simple current SBER-vs-quarterly diagnostics using key rate 14% as a baseline proxy only.
    # Dividend adjustment is deliberately left as scenario=0 here; backtest must add known dividend cashflows.
    by={r["instrument"]:r for r in rows}
    spot=by.get("SBER",{})
    spot_ask=spot.get("ask") or spot.get("last")
    if not spot_ask:return
    expiry_map={"SBRF1226":datetime(2026,12,18,tzinfo=timezone.utc),
                "SBRF0327":datetime(2027,3,19,tzinfo=timezone.utc),
                "SBRF0627":datetime(2027,6,18,tzinfo=timezone.utc)}
    diag=[]
    nowdt=datetime.now(timezone.utc)
    for k,exp in expiry_map.items():
        r=by.get(k,{})
        fut=(r.get("bid") or r.get("last"))
        if not fut:continue
        # SBRF quote is approximately 100 shares * rubles/share.
        fut_per_share=fut/100.0
        T=max(0.0,(exp-nowdt).total_seconds()/(365.0*86400))
        fair=spot_ask*math.exp(0.14*T)
        raw_basis=(fut_per_share/spot_ask-1)
        fair_basis=(fair/spot_ask-1)
        diag.append({
          "utc":utc(),"instrument":k,"spot_ask":spot_ask,"fut_bid_per_share":fut_per_share,
          "days_to_expiry":round(T*365,2),"raw_basis_pct":raw_basis*100,
          "fair_basis_keyrate14_no_div_pct":fair_basis*100,
          "excess_basis_pct_before_costs_dividends":(raw_basis-fair_basis)*100
        })
    if diag:
        with (OUT/"moex_m01_current_diagnostic.csv").open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(diag[0].keys()));w.writeheader();w.writerows(diag)

if __name__=="__main__":
    raw=current_snapshot()
    build_current(raw)
