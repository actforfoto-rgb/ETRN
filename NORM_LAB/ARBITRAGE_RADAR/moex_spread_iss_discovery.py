from __future__ import annotations

import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-SPREAD-ISS-DISCOVERY/1.0"})

SPREAD="SiZ6SiH7"

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=25)
    r.raise_for_status()
    return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def main():
    report={"secid":SPREAD,"generic":{},"search":{},"routes":[]}

    # Generic security discovery.
    try:
        j=get(f"{BASE}/securities/{SPREAD}.json",{"iss.meta":"off"})
        report["generic"]={
          k:rows(j,k) for k in ("description","boards","securities")
          if k in j
        }
    except Exception as e:
        report["generic_error"]=f"{type(e).__name__}: {e}"

    # Search endpoint may expose exact canonical spelling / group.
    try:
        j=get(f"{BASE}/securities.json",{"q":SPREAD,"iss.meta":"off"})
        report["search"]={"securities":rows(j,"securities")[:100]}
    except Exception as e:
        report["search_error"]=f"{type(e).__name__}: {e}"

    # Probe market-level route from MOEX documentation.
    probes=[
      ("forts_market",f"{BASE}/engines/futures/markets/forts/securities/{SPREAD}.json"),
      ("forts_RFUD",f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{SPREAD}.json"),
    ]

    boards=report.get("generic",{}).get("boards",[])
    for b in boards:
        bid=b.get("boardid") or b.get("BOARDID")
        eng=b.get("engine") or b.get("engine_name") or b.get("ENGINE")
        mkt=b.get("market") or b.get("market_name") or b.get("MARKET")
        if bid and eng and mkt:
            probes.append((f"discovered_{bid}",
                           f"{BASE}/engines/{eng}/markets/{mkt}/boards/{bid}/securities/{SPREAD}.json"))

    seen=set()
    for label,url in probes:
        if url in seen:continue
        seen.add(url)
        try:
            j=get(url,{"iss.meta":"off"})
            sec=rows(j,"securities")
            md=rows(j,"marketdata")
            report["routes"].append({
              "label":label,"url":url,"ok":True,
              "security_rows":len(sec),"marketdata_rows":len(md),
              "security":sec[:3],"marketdata":md[:3]
            })
        except Exception as e:
            report["routes"].append({
              "label":label,"url":url,"ok":False,
              "error":f"{type(e).__name__}: {e}"[:700]
            })

    # Probe candles on any likely route without board.
    try:
        j=get(f"{BASE}/engines/futures/markets/forts/securities/{SPREAD}/candles.json",
              {"from":"2026-09-29","till":"2026-09-30","interval":1,"iss.meta":"off"})
        rr=rows(j,"candles")
        report["candles"]={"rows":len(rr),"sample":rr[-10:]}
    except Exception as e:
        report["candles_error"]=f"{type(e).__name__}: {e}"

    (OUT/"moex_spread_iss_discovery.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "generic":report.get("generic"),
      "search":report.get("search"),
      "routes":[{"label":x["label"],"ok":x["ok"],
                 "security_rows":x.get("security_rows"),
                 "marketdata_rows":x.get("marketdata_rows"),
                 "error":x.get("error")} for x in report["routes"]],
      "candles":report.get("candles")
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
