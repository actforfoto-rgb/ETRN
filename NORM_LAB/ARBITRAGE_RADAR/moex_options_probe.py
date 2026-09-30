from __future__ import annotations

import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-OPTIONS-PROBE/1.0"})
BASE="https://iss.moex.com/iss"

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=30);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def main():
    report={}
    urls=[
      f"{BASE}/engines/futures/markets/options/securities.json",
      f"{BASE}/engines/futures/markets/options/boards/ROPD/securities.json",
    ]
    for url in urls:
      try:
        j=get(url,{"iss.meta":"off","iss.only":"securities,marketdata"})
        sec=rows(j,"securities");md=rows(j,"marketdata")
        report[url]={
          "ok":True,
          "security_columns":j.get("securities",{}).get("columns",[]),
          "marketdata_columns":j.get("marketdata",{}).get("columns",[]),
          "security_count":len(sec),"marketdata_count":len(md),
          "securities_sample":sec[:30],"marketdata_sample":md[:30],
          "liquid":[m for m in md if (m.get("NUMTRADES") or 0)>0][:100]
        }
      except Exception as e:
        report[url]={"ok":False,"error":f"{type(e).__name__}: {e}"}
    (OUT/"moex_options_probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:{"ok":v.get("ok"),"security_count":v.get("security_count"),"marketdata_count":v.get("marketdata_count"),"error":v.get("error")} for k,v in report.items()},ensure_ascii=False,indent=2))

if __name__=="__main__":main()
