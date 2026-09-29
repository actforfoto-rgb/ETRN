from __future__ import annotations

import csv, json, math, statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
ISS="https://iss.moex.com/iss"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})

START="2026-07-21"  # after 2026 SBER dividend record; avoids ex-div cashflow in this first diagnostic
END="2026-09-29"
CONTRACTS={
 "SBRF-12.26":{"secid":"SRZ6","expiry":"2026-12-18"},
 "SBRF-3.27":{"secid":"SRH7","expiry":"2027-03-19"},
 "SBRF-6.27":{"secid":"SRM7","expiry":"2027-06-18"},
}
LOT=100.0
SPOT_TAKER=0.0003
FUT_TAKER=0.000198
BUFFER_BPS=5.0

def block(j,name):
    x=j.get(name,{})
    return [dict(zip(x.get("columns",[]),r)) for r in x.get("data",[])]

def paged(url):
    out=[];start=0
    while True:
        sep="&" if "?" in url else "?"
        r=S.get(f"{url}{sep}iss.meta=off&start={start}",timeout=30);r.raise_for_status()
        rows=block(r.json(),"history");out.extend(rows)
        if len(rows)<100:break
        start+=len(rows)
    return out

def ruonia():
    p={"UniDbQuery.Posted":"True","UniDbQuery.From":"21.07.2026","UniDbQuery.To":"29.09.2026"}
    r=S.get("https://www.cbr.ru/hd_base/ruonia/dynamics/",params=p,timeout=30);r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser");d={}
    for tr in soup.find_all("tr"):
        td=[x.get_text(" ",strip=True) for x in tr.find_all("td")]
        if len(td)>=2:
            try:d[datetime.strptime(td[0],"%d.%m.%Y").date()]=float(td[1].replace(",",".").replace(" ",""))
            except:pass
    return d

def prev(rate,d):
    x=d
    for _ in range(14):
        if x in rate:return rate[x]
        x-=timedelta(days=1)

def f(v):
    try:return float(v)
    except:return None

def main():
    sr=paged(f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json?from={START}&till={END}")
    sm={r["TRADEDATE"]:r for r in sr}
    rate=ruonia()
    allrows=[];summary=[]
    fixed_pct=2*SPOT_TAKER+2*FUT_TAKER+BUFFER_BPS/10000
    for name,cfg in CONTRACTS.items():
        hr=paged(f"{ISS}/history/engines/futures/markets/forts/boards/RFUD/securities/{cfg['secid']}.json?from={START}&till={END}")
        fm={r["TRADEDATE"]:r for r in hr}
        exp=datetime.fromisoformat(cfg["expiry"]).date()
        xs=[]
        for ds in sorted(set(sm)&set(fm)):
            d=datetime.fromisoformat(ds).date()
            T=(exp-d).days/365.0
            if T<=0:continue
            s=sm[ds];q=fm[ds]
            spot=f(s.get("CLOSE") or s.get("LEGALCLOSEPRICE") or s.get("WAPRICE"))
            fut=f(q.get("SETTLEPRICE") or q.get("CLOSE") or q.get("WAPRICE"))
            rr=prev(rate,d)
            if not spot or not fut or rr is None:continue
            fp=fut/LOT
            observed=fp/spot-1
            fair=math.exp((rr/100)*T)-1
            excess=observed-fair
            net=excess-fixed_pct
            ann=net/T
            row={
              "date":ds,"contract":name,"secid":cfg["secid"],"expiry":cfg["expiry"],
              "spot_close":spot,"futures_settle":fut,"futures_per_share":fp,
              "days_to_expiry":(exp-d).days,"ruonia_pct":rr,
              "observed_basis_pct":observed*100,"fair_basis_ruonia_no_announced_div_pct":fair*100,
              "excess_basis_before_cost_pct":excess*100,
              "fixed_exchange_plus_buffer_pct":fixed_pct*100,
              "diagnostic_net_pct":net*100,"simple_annualized_diagnostic_net_pct":ann*100,
              "futures_volume":f(q.get("VOLUME")),"open_interest":f(q.get("OPENPOSITION"))
            }
            allrows.append(row);xs.append(row)
        if xs:
            nets=[x["diagnostic_net_pct"] for x in xs]
            summary.append({
              "contract":name,"secid":cfg["secid"],"n_days":len(xs),
              "positive_net_days":sum(x>0 for x in nets),
              "positive_net_pct":100*sum(x>0 for x in nets)/len(nets),
              "median_diagnostic_net_pct":statistics.median(nets),
              "max_diagnostic_net_pct":max(nets),
              "median_annualized_net_pct":statistics.median(x["simple_annualized_diagnostic_net_pct"] for x in xs),
              "max_annualized_net_pct":max(x["simple_annualized_diagnostic_net_pct"] for x in xs)
            })
    if allrows:
        with (OUT/"moex_quarterly_basis_daily.csv").open("w",newline="",encoding="utf-8") as g:
            w=csv.DictWriter(g,fieldnames=list(allrows[0].keys()));w.writeheader();w.writerows(allrows)
    with (OUT/"moex_quarterly_basis_summary.json").open("w",encoding="utf-8") as g:
        json.dump({
          "period":[START,END],
          "fee_model":{"spot_taker_each_side":SPOT_TAKER,"equity_futures_taker_each_side":FUT_TAKER,"execution_buffer_bps":BUFFER_BPS},
          "dividend_model":"No announced dividend cashflow inserted in this first post-2026-record diagnostic; point-in-time dividend expectations must be added before executable classification.",
          "summary":summary,
          "limitations":[
            "Daily stock close and futures settlement are not synchronized executable bid/ask quotes.",
            "RUONIA is a benchmark proxy, not executable account funding.",
            "Broker commissions, taxes, margin collateral remuneration and delivery mechanics are excluded.",
            "This output can rank periods for intraday replay but cannot label a trade executable."
          ]
        },g,ensure_ascii=False,indent=2)

if __name__=="__main__":
    main()
