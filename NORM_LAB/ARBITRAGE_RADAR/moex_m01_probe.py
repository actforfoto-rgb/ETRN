from __future__ import annotations

import csv
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

def utc(): return datetime.now(timezone.utc).isoformat()

def get(url, params=None, timeout=20):
    r=S.get(url,params=params or {},timeout=timeout)
    r.raise_for_status()
    return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,r)) for r in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def field(row,*names):
    for n in names:
        v=row.get(n)
        if v not in (None,""): return v
    return None

def search(q):
    j=get(f"{BASE}/securities.json",{"q":q,"iss.meta":"off"})
    return rows(j,"securities")

def current(secid,engine,market,board=None):
    if board:
        url=f"{BASE}/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
    else:
        url=f"{BASE}/engines/{engine}/markets/{market}/securities/{secid}.json"
    return url,get(url,{"iss.meta":"off"})

def history(secid,engine,market,board=None,frm="2026-01-01",till="2026-09-29"):
    if board:
        url=f"{BASE}/history/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
    else:
        url=f"{BASE}/history/engines/{engine}/markets/{market}/securities/{secid}.json"
    out=[];start=0
    while True:
        j=get(url,{"from":frm,"till":till,"start":start,"iss.meta":"off"})
        rr=rows(j,"history")
        out.extend(rr)
        cur=rows(j,"history.cursor")
        total=int(cur[0].get("TOTAL") or 0) if cur else len(out)
        if not rr or len(out)>=total: break
        start=len(out)
    return url,out

def resolve_contract(label):
    hits=search(label)
    # Prefer futures contracts and exact display names.
    def score(r):
        txt=" ".join(str(r.get(k) or "") for k in ["SECID","SHORTNAME","NAME"]).upper()
        s=0
        if label.upper() in txt: s-=10
        if str(r.get("GROUP","")).lower().find("future")>=0: s-=5
        if r.get("IS_TRADED") in (1,"1"): s-=2
        return s
    hits=sorted(hits,key=score)
    return hits[0] if hits else None,hits[:20]

def market_row(j):
    rr=rows(j,"marketdata")
    return rr[0] if rr else {}

def security_row(j):
    rr=rows(j,"securities")
    return rr[0] if rr else {}

def get_px(r,side):
    names={"bid":("BID","BESTBID"),"ask":("OFFER","ASK","BESTASK"),
           "last":("LAST","SETTLEPRICE","SETTLEPRICE_CLR","LCURRENTPRICE")}
    return num(field(r,*names[side]))

def main():
    query_terms=["SBRF","SBRF-12.26","SBRF-3.27","SBRF-6.27","SRZ6","SRH7","SRM7",
                 "SBRF-12.26-3.27","календарный спред SBRF"]
    search_report={}
    for q in query_terms:
        try: search_report[q]=search(q)
        except Exception as e: search_report[q]=[{"error":f"{type(e).__name__}: {e}"}]
    (OUT/"moex_m01_security_search.json").write_text(
        json.dumps(search_report,ensure_ascii=False,indent=2),encoding="utf-8")

    mapping={"SBER":"SBER","SBERF":"SBERF"}
    resolution={}
    for label in ["SBRF-12.26","SBRF-3.27","SBRF-6.27"]:
        hit,hits=resolve_contract(label)
        resolution[label]={"chosen":hit,"candidates":hits}
        if hit and hit.get("SECID"):
            mapping[label]=hit["SECID"]
    (OUT/"moex_m01_contract_mapping.json").write_text(
        json.dumps({"mapping":mapping,"resolution":resolution},ensure_ascii=False,indent=2),encoding="utf-8")

    probe=[]
    current_rows=[]
    histories={}

    targets=[
      ("SBER",mapping["SBER"],"stock","shares","TQBR"),
      ("SBERF",mapping["SBERF"],"futures","forts","RFUD")
    ]
    for label in ["SBRF-12.26","SBRF-3.27","SBRF-6.27"]:
        if label in mapping:
            targets.append((label,mapping[label],"futures","forts","RFUD"))

    for label,secid,engine,market,board in targets:
        try:
            url,j=current(secid,engine,market,board)
            sr=security_row(j);mr=market_row(j)
            current_rows.append({
              "utc":utc(),"label":label,"secid":secid,"board":board,
              "bid":get_px(mr,"bid"),"ask":get_px(mr,"ask"),"last":get_px(mr,"last"),
              "settle":num(field(mr,"SETTLEPRICE","SETTLEPRICE_CLR")),
              "swaprate":num(field(mr,"SWAPRATE","SWAPRATE_CURR")),
              "volume":num(field(mr,"VOLTODAY","VOLUME")),
              "numtrades":num(field(mr,"NUMTRADES")),
              "openposition":num(field(mr,"OPENPOSITION")),
              "expiry":field(sr,"LASTTRADEDATE","LASTDELDATE"),
              "lotvolume":field(sr,"LOTVOLUME","LOTSIZE"),
              "initialmargin":field(sr,"INITIALMARGIN"),
              "buysellfee":field(sr,"BUYSELLFEE"),
              "scalperfee":field(sr,"SCALPERFEE"),
              "stepprice":field(sr,"STEPPRICE"),
            })
            probe.append({"label":label,"secid":secid,"current_ok":True,"current_url":url,
                          "current_rows":len(rows(j,"marketdata"))})
        except Exception as e:
            probe.append({"label":label,"secid":secid,"current_ok":False,
                          "error":f"{type(e).__name__}: {e}"[:700]})

        try:
            hurl,h=history(secid,engine,market,board)
            histories[label]=h
            probe[-1]["history_url"]=hurl
            probe[-1]["history_rows"]=len(h)
            probe[-1]["history_first"]=h[0].get("TRADEDATE") if h else None
            probe[-1]["history_last"]=h[-1].get("TRADEDATE") if h else None
        except Exception as e:
            probe[-1]["history_error"]=f"{type(e).__name__}: {e}"[:700]

    (OUT/"moex_m01_probe.json").write_text(json.dumps(probe,ensure_ascii=False,indent=2),encoding="utf-8")

    if current_rows:
        p=OUT/"moex_m01_current.csv"
        with p.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(current_rows[0].keys()));w.writeheader();w.writerows(current_rows)

    # Daily raw basis joined to SBER. Quarterly SBRF quotes are per 100 shares.
    spot_hist={r.get("TRADEDATE"):r for r in histories.get("SBER",[])}
    basis=[]
    for label,h in histories.items():
        if label=="SBER": continue
        for r in h:
            d=r.get("TRADEDATE"); s=spot_hist.get(d)
            if not s: continue
            sp=num(field(s,"LEGALCLOSEPRICE","WAPRICE","CLOSE"))
            fp=num(field(r,"SETTLEPRICE","WAPRICE","CLOSE"))
            if not sp or not fp: continue
            per_share=fp/100.0 if label.startswith("SBRF-") else fp
            basis.append({
              "date":d,"instrument":label,"spot_price":sp,"future_per_share":per_share,
              "raw_basis_pct":(per_share/sp-1)*100,
              "futures_volume":r.get("VOLUME"),"futures_numtrades":r.get("NUMTRADES"),
              "openposition":r.get("OPENPOSITION"),"swaprate":r.get("SWAPRATE")
            })
    if basis:
        p=OUT/"moex_m01_daily_raw_basis.csv"
        with p.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(basis[0].keys()));w.writeheader();w.writerows(basis)
        by={}
        for r in basis: by.setdefault(r["instrument"],[]).append(float(r["raw_basis_pct"]))
        sm=[]
        for k,x in sorted(by.items()):
            sm.append({"instrument":k,"n":len(x),"min_pct":min(x),
                       "median_pct":statistics.median(x),"mean_pct":statistics.fmean(x),"max_pct":max(x)})
        with (OUT/"moex_m01_daily_raw_basis_summary.csv").open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(sm[0].keys()));w.writeheader();w.writerows(sm)

if __name__=="__main__":
    main()
