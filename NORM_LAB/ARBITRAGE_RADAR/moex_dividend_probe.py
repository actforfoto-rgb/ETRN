from __future__ import annotations

import csv, json
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_history(secid,frm="2025-01-01",till="2026-09-30"):
    candidates=[
      f"{BASE}/history/engines/stock/markets/index/boards/SNDX/securities/{secid}.json",
      f"{BASE}/history/engines/stock/markets/index/securities/{secid}.json",
    ]
    errors=[]
    for url in candidates:
        try:
            out=[];start=0
            while True:
                j=get(url,{"from":frm,"till":till,"start":start,"iss.meta":"off"})
                rr=rows(j,"history")
                if not rr:
                    break
                out.extend(rr)
                cur=rows(j,"history.cursor")
                total=int(cur[0].get("TOTAL") or 0) if cur else len(out)
                if len(out)>=total:break
                start=len(out)
            if out:
                return url,out
        except Exception as e:
            errors.append(f"{url}: {type(e).__name__}: {e}")
    raise RuntimeError(" || ".join(errors) if errors else "empty history")

def numeric_candidate(row):
    preferred=[
      "CLOSE","CURRENTVALUE","LASTVALUE","WAPRICE","LEGALCLOSEPRICE",
      "OPEN","HIGH","LOW"
    ]
    vals={}
    for k in preferred:
        v=row.get(k)
        if v not in (None,""):
            try: vals[k]=float(v)
            except: pass
    return vals

def main():
    url,rr=fetch_history("IMOEXDIV")
    out=[]
    for r in rr:
        vals=numeric_candidate(r)
        row={"TRADEDATE":r.get("TRADEDATE"),"SECID":r.get("SECID"),**vals}
        out.append(row)

    all_fields=[]
    for r in out:
        for k in r:
            if k not in all_fields:all_fields.append(k)

    with (OUT/"imoexdiv_history_probe.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=all_fields)
        w.writeheader();w.writerows(out)

    # Preserve raw column list and a compact non-zero diagnostic.
    raw_cols=sorted(set(k for r in rr for k in r.keys()))
    nonzero=[]
    for r in out:
        nz={k:v for k,v in r.items() if k not in ("TRADEDATE","SECID") and isinstance(v,(int,float)) and abs(v)>1e-12}
        if nz:
            nonzero.append({"date":r["TRADEDATE"],**nz})

    report={
      "url":url,
      "rows":len(rr),
      "first_date":rr[0].get("TRADEDATE") if rr else None,
      "last_date":rr[-1].get("TRADEDATE") if rr else None,
      "raw_columns":raw_cols,
      "sample_first":rr[:5],
      "sample_last":rr[-5:],
      "nonzero_count":len(nonzero),
      "nonzero_first_30":nonzero[:30],
      "nonzero_last_30":nonzero[-30:],
    }
    (OUT/"imoexdiv_history_probe.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"
    )
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
