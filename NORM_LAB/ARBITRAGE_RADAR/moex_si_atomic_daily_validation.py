from __future__ import annotations

import json, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results";OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session();S.headers.update({"User-Agent":"NORM-LAB-SI-ATOMIC-DAILY-VALIDATION/1.0"})
ISS="https://iss.moex.com/iss"
ARCHIVE="https://www.moex.com/ru/derivatives/spreads/archive-spreads.aspx?code=Si-12.26-3.27"
NEAR="SiZ6";FAR="SiH7"

def num(x):
    if x is None:return None
    s=str(x).replace("\xa0","").replace(" ","").replace(",",".").strip()
    if s in ("","-","—"):return None
    try:return float(s)
    except:return None

def get_json(url,params=None):
    r=S.get(url,params=params or {},timeout=30);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def fut_daily(secid,days=60):
    till=datetime.now(timezone.utc)
    frm=till-timedelta(days=days)
    url=f"{ISS}/history/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json"
    out=[];start=0
    while True:
        j=get_json(url,{"from":frm.strftime("%Y-%m-%d"),"till":till.strftime("%Y-%m-%d"),
                        "start":start,"iss.meta":"off"})
        rr=rows(j,"history");out.extend(rr)
        cur=rows(j,"history.cursor")
        total=int(cur[0].get("TOTAL") or 0) if cur else len(out)
        if not rr or len(out)>=total:break
        start=len(out)
    d={}
    for r in out:
        dt=r.get("TRADEDATE")
        px=None
        for k in ("SETTLEPRICE","CLOSE","WAPRICE"):
            px=num(r.get(k))
            if px is not None:break
        if dt and px is not None:d[dt]=px
    return d

def archive_rows():
    r=S.get(ARCHIVE,timeout=30);r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    out=[]
    for tr in soup.find_all("tr"):
        cells=[x.get_text(" ",strip=True) for x in tr.find_all(["td","th"])]
        if len(cells)<10:continue
        if cells[0].strip()!="Si-12.26-3.27":continue
        try:dt=datetime.strptime(cells[1].strip(),"%d.%m.%Y").date().isoformat()
        except:continue
        out.append({
          "date":dt,"last":num(cells[2]),"bid":num(cells[3]),"ask":num(cells[4]),
          "high":num(cells[5]),"low":num(cells[6]),"amount":num(cells[7]),
          "volume_rub":num(cells[8]),"trades":num(cells[9])
        })
    return out

def main():
    A=fut_daily(NEAR);B=fut_daily(FAR);arc=archive_rows()
    joined=[]
    for r in arc:
        d=r["date"]
        if d not in A or d not in B:continue
        synth=B[d]-A[d]
        last=r["last"]
        diff=(last-synth) if last not in (None,0) else None
        width=(r["ask"]-r["bid"]) if r["ask"] is not None and r["bid"] is not None else None
        joined.append({**r,"near_close":A[d],"far_close":B[d],"synthetic_close":synth,
                       "atomic_minus_synthetic":diff,"closing_width":width})
    valid=[r for r in joined if r["atomic_minus_synthetic"] is not None]
    diffs=[r["atomic_minus_synthetic"] for r in valid]
    active=[r for r in valid if (r["trades"] or 0)>0]
    adiffs=[r["atomic_minus_synthetic"] for r in active]
    widths=[r["closing_width"] for r in active if r["closing_width"] is not None]
    report={
      "archive_rows":len(arc),"joined_days":len(joined),"valid_atomic_last_days":len(valid),
      "active_trade_days":len(active),
      "all_valid":{
        "median_abs_diff_points":statistics.median(abs(x) for x in diffs) if diffs else None,
        "mean_abs_diff_points":statistics.fmean(abs(x) for x in diffs) if diffs else None,
        "max_abs_diff_points":max(abs(x) for x in diffs) if diffs else None,
        "within_1_point_pct":100*sum(abs(x)<=1 for x in diffs)/len(diffs) if diffs else None,
        "within_5_points_pct":100*sum(abs(x)<=5 for x in diffs)/len(diffs) if diffs else None,
      },
      "active_days":{
        "median_abs_diff_points":statistics.median(abs(x) for x in adiffs) if adiffs else None,
        "mean_abs_diff_points":statistics.fmean(abs(x) for x in adiffs) if adiffs else None,
        "max_abs_diff_points":max(abs(x) for x in adiffs) if adiffs else None,
        "within_1_point_pct":100*sum(abs(x)<=1 for x in adiffs)/len(adiffs) if adiffs else None,
        "within_5_points_pct":100*sum(abs(x)<=5 for x in adiffs)/len(adiffs) if adiffs else None,
        "median_closing_width_points":statistics.median(widths) if widths else None,
      },
      "recent":joined[-30:]
    }
    (OUT/"moex_si_atomic_daily_validation.json").write_text(
      json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
