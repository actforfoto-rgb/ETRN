from __future__ import annotations

import json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
BASE="https://iss.moex.com/iss"
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})

PERPS=["BTCUSDF","ETHUSDF","SOLUSDF","XRPUSDF","TRXUSDF"]
FIXED=["BTU6","BTV6","BTX6","BTZ6","EHU6","EHV6","EHX6","EHZ6","S3U6","S3V6","S3X6","S3Z6","XRU6","XRV6","XRX6","XRZ6","TXU6","TXV6","TXX6","TXZ6"]

def get(url,params=None):
 r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
 b=j.get(name,{})
 cols=b.get("columns",[])
 return [dict(zip(cols,x)) for x in b.get("data",[])]

def main():
 report={"perpetuals":{},"fixed":{}}
 for sid in PERPS:
  try:
   j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{sid}.json",{"iss.meta":"off"})
   sec=rows(j,"securities");md=rows(j,"marketdata")
   h=get(f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/securities/{sid}.json",
         {"from":"2026-08-01","till":"2026-09-30","iss.meta":"off"})
   hr=rows(h,"history")
   report["perpetuals"][sid]={
    "security":sec[0] if sec else None,
    "marketdata":md[0] if md else None,
    "history_columns":list(hr[0].keys()) if hr else [],
    "history_first":hr[:3],
    "history_last":hr[-5:],
    "history_rows_page":len(hr)
   }
  except Exception as e:
   report["perpetuals"][sid]={"error":f"{type(e).__name__}: {e}"}

 for sid in FIXED:
  try:
   j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{sid}.json",{"iss.meta":"off"})
   sec=rows(j,"securities");md=rows(j,"marketdata")
   if sec:
    report["fixed"][sid]={"security":sec[0],"marketdata":md[0] if md else None}
  except Exception:
   pass

 (OUT/"moex_crypto_perp_probe.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({
   "perpetuals":{
    k:{
      "marketdata":v.get("marketdata"),
      "history_columns":v.get("history_columns"),
      "history_first":v.get("history_first"),
      "history_last":v.get("history_last")
    } for k,v in report["perpetuals"].items()
   },
   "fixed_found":list(report["fixed"].keys())
 },ensure_ascii=False,indent=2))

if __name__=="__main__":main()
