from __future__ import annotations

import csv
import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

# Map FORTS ASSETCODE -> MOEX stock SECID where naming differs.
# Unknowns stay unresolved and are preserved in output.
ASSET_TO_SPOT = {
    "SBRF":"SBER",
    "SBPR":"SBERP",
    "GAZR":"GAZP",
    "GMKN":"GMKN",
    "LKOH":"LKOH",
    "ROSN":"ROSN",
    "YDEX":"YDEX",
    "VTBR":"VTBR",
    "NOTK":"NVTK",
    "PLZL":"PLZL",
    "FEES":"FEES",
    "HEAD":"HEAD",
    "SIBN":"SIBN",
    "SOFL":"SOFL",
    "AFLT":"AFLT",
    "AFKS":"AFKS",
    "TATN":"TATN",
    "TATP":"TATNP",
    "RTKM":"RTKM",
    "RTKMP":"RTKMP",
    "MGNT":"MGNT",
    "MTSS":"MTSS",
    "MOEX":"MOEX",
    "NLMK":"NLMK",
    "CHMF":"CHMF",
    "ALRS":"ALRS",
    "PHOR":"PHOR",
    "RUAL":"RUAL",
    "IRAO":"IRAO",
    "PIKK":"PIKK",
    "MAGN":"MAGN",
    "FLOT":"FLOT",
    "HYDR":"HYDR",
    "TRNF":"TRNFP",
}

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=25)
    r.raise_for_status()
    return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,r)) for r in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def main():
    # Pull all current FORTS futures securities + marketdata.
    j=get(f"{BASE}/engines/futures/markets/forts/securities.json",{
        "iss.meta":"off","iss.only":"securities,marketdata"
    })
    sec=rows(j,"securities")
    md={r.get("SECID"):r for r in rows(j,"marketdata")}

    # Keep only equity futures with an underlying stock we can resolve.
    out=[]
    for s in sec:
        secid=s.get("SECID")
        asset=s.get("ASSETCODE")
        short=s.get("SHORTNAME") or ""
        stype=s.get("SECTYPE")
        # Exclude options/spreads/perpetual here; this file ranks fixed-expiry stock futures.
        if not secid or not asset:
            continue
        if secid in ("SBERF","GAZPF"):
            continue
        spot=ASSET_TO_SPOT.get(asset)
        if not spot:
            continue
        m=md.get(secid,{})
        volume=num(m.get("VOLTODAY") or m.get("VOLUME")) or 0
        value=num(m.get("VALTODAY") or m.get("VALUE")) or 0
        trades=num(m.get("NUMTRADES")) or 0
        oi=num(m.get("OPENPOSITION")) or 0
        bid=num(m.get("BID")); ask=num(m.get("OFFER")); last=num(m.get("LAST"))
        if not any([volume,value,trades,oi,bid,ask,last]):
            continue
        out.append({
            "assetcode":asset,"spot_secid":spot,"future_secid":secid,
            "shortname":short,"expiry":s.get("LASTTRADEDATE"),
            "lotvolume":s.get("LOTVOLUME"),"initialmargin":s.get("INITIALMARGIN"),
            "buysellfee":s.get("BUYSELLFEE"),"scalperfee":s.get("SCALPERFEE"),
            "bid":bid,"ask":ask,"last":last,"volume":volume,"value":value,
            "numtrades":trades,"openposition":oi,
        })

    # Prefer front/liquid contracts; rank primarily by traded value then OI.
    out.sort(key=lambda r:(r["value"],r["openposition"],r["numtrades"]),reverse=True)

    with (OUT/"moex_equity_futures_universe.csv").open("w",newline="",encoding="utf-8") as f:
        fields=list(out[0].keys()) if out else ["assetcode"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)

    # One best current contract per underlying.
    best={}
    for r in out:
        best.setdefault(r["spot_secid"],r)
    leaders=list(best.values())
    leaders.sort(key=lambda r:(r["value"],r["openposition"]),reverse=True)
    with (OUT/"moex_equity_underlying_leaders.csv").open("w",newline="",encoding="utf-8") as f:
        fields=list(leaders[0].keys()) if leaders else ["assetcode"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(leaders)

    summary={
        "futures_contracts":len(out),
        "unique_stock_underlyings":len(best),
        "top_underlyings":[
            {"spot":r["spot_secid"],"future":r["future_secid"],"value":r["value"],
             "volume":r["volume"],"openposition":r["openposition"],"numtrades":r["numtrades"]}
            for r in leaders[:20]
        ],
        "perpetual_equity_pairs":[
            {"spot":"SBER","perpetual":"SBERF"},
            {"spot":"GAZP","perpetual":"GAZPF"}
        ]
    }
    (OUT/"moex_equity_universe_summary.json").write_text(
        json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
