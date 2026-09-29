from __future__ import annotations

import csv, json, statistics, math
from datetime import datetime, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
ISS="https://iss.moex.com/iss"
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})

DATE_FROM="2025-01-01"
DATE_TO="2026-09-29"
LOT=100.0
SPOT_TAKER=0.0003
# Use instrument card BUYSELLFEE if available; fallback approx current 8.2 RUB/contract/side.
FUT_FEE_SIDE_RUB=8.2
EXEC_BUFFER_BPS=5.0

def block(j,name):
    x=j.get(name,{})
    return [dict(zip(x.get("columns",[]),r)) for r in x.get("data",[])]

def getj(url):
    r=S.get(url,timeout=30);r.raise_for_status();return r.json()

def paged_history(urlbase):
    out=[];start=0
    while True:
        sep="&" if "?" in urlbase else "?"
        j=getj(f"{urlbase}{sep}start={start}&iss.meta=off")
        rows=block(j,"history");out.extend(rows)
        if len(rows)<100:break
        start += len(rows)
        if start>5000:break
    return out

def fetch_stock():
    return paged_history(
      f"{ISS}/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json?from={DATE_FROM}&till={DATE_TO}"
    )

def fetch_perp():
    return paged_history(
      f"{ISS}/history/engines/futures/markets/forts/boards/RFUD/securities/SBERF.json?from={DATE_FROM}&till={DATE_TO}"
    )

def fetch_ruonia():
    params={"UniDbQuery.Posted":"True","UniDbQuery.From":"01.01.2025","UniDbQuery.To":"29.09.2026"}
    r=S.get("https://www.cbr.ru/hd_base/ruonia/dynamics/",params=params,timeout=30)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    rates={}
    for tr in soup.find_all("tr"):
        tds=[x.get_text(" ",strip=True) for x in tr.find_all("td")]
        if len(tds)<2:continue
        try:
            d=datetime.strptime(tds[0],"%d.%m.%Y").date()
            v=float(tds[1].replace(" ","").replace(",","."))
            rates[d]=v
        except:pass
    return rates

def previous_rate(rates,d):
    x=d
    for _ in range(14):
        if x in rates:return rates[x]
        x-=timedelta(days=1)
    return None

def n(v):
    try:return float(v)
    except:return None

def main():
    stock=fetch_stock();perp=fetch_perp();rates=fetch_ruonia()
    sm={r["TRADEDATE"]:r for r in stock}
    pm={r["TRADEDATE"]:r for r in perp}
    dates=sorted(set(sm)&set(pm))
    rows=[]
    for ds in dates:
        s=sm[ds];p=pm[ds]
        spot=n(s.get("CLOSE") or s.get("LEGALCLOSEPRICE") or s.get("WAPRICE"))
        settle=n(p.get("SETTLEPRICE") or p.get("CLOSE") or p.get("WAPRICE"))
        swap=n(p.get("SWAPRATE_CURR") if p.get("SWAPRATE_CURR") is not None else p.get("SWAPRATE"))
        if spot is None or settle is None or swap is None:continue
        d=datetime.fromisoformat(ds).date()
        kr=previous_rate(rates,d)
        if kr is None:continue
        premium=settle-spot
        fund_ann=(swap/spot)*365*100 if spot else None
        ruonia_daily_rub=spot*LOT*(kr/100)/365
        fund_rub=swap*LOT
        excess_day=fund_rub-ruonia_daily_rub
        fixed_roundtrip=2*SPOT_TAKER*spot*LOT + 2*FUT_FEE_SIDE_RUB + EXEC_BUFFER_BPS/10000*spot*LOT
        rows.append({
          "date":ds,"spot_close":spot,"perp_settle":settle,"premium_rub_per_share":premium,
          "premium_bps":premium/spot*10000,"swaprate_rub_per_share":swap,
          "funding_rub_per_contract":fund_rub,"funding_annualized_pct":fund_ann,
          "ruonia_pct":kr,"capital_cost_rub_per_day":ruonia_daily_rub,
          "funding_minus_ruonia_rub_per_day":excess_day,
          "funding_minus_ruonia_annualized_pct":fund_ann-kr,
          "roundtrip_exchange_plus_buffer_rub":fixed_roundtrip,
          "perp_volume":n(p.get("VOLUME")),"perp_open_interest":n(p.get("OPENPOSITION"))
        })
    path=OUT/"moex_sberf_daily.csv"
    if rows:
        with path.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)

    xs=[r["funding_minus_ruonia_annualized_pct"] for r in rows]
    pos=[r for r in rows if r["funding_minus_ruonia_rub_per_day"]>0]
    # contiguous positive-excess runs; asks whether daily funding could amortize entry/exit costs
    runs=[];cur=[]
    for r in rows:
        if r["funding_minus_ruonia_rub_per_day"]>0:
            if cur and (datetime.fromisoformat(r["date"]).date()-datetime.fromisoformat(cur[-1]["date"]).date()).days<=4:
                cur.append(r)
            else:
                if cur:runs.append(cur)
                cur=[r]
        else:
            if cur:runs.append(cur);cur=[]
    if cur:runs.append(cur)

    runout=[]
    for rr in runs:
        cum=sum(x["funding_minus_ruonia_rub_per_day"] for x in rr)
        fee=rr[0]["roundtrip_exchange_plus_buffer_rub"]
        runout.append({
          "start":rr[0]["date"],"end":rr[-1]["date"],"trading_days":len(rr),
          "cum_funding_minus_ruonia_rub":round(cum,4),
          "entry_exit_exchange_plus_buffer_rub":round(fee,4),
          "net_after_fixed_cost_rub":round(cum-fee,4),
          "avg_excess_annualized_pct":round(statistics.fmean(x["funding_minus_ruonia_annualized_pct"] for x in rr),4),
          "max_excess_annualized_pct":round(max(x["funding_minus_ruonia_annualized_pct"] for x in rr),4)
        })
    with (OUT/"moex_sberf_positive_runs.csv").open("w",newline="",encoding="utf-8") as f:
        fields=["start","end","trading_days","cum_funding_minus_ruonia_rub","entry_exit_exchange_plus_buffer_rub","net_after_fixed_cost_rub","avg_excess_annualized_pct","max_excess_annualized_pct"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(runout)

    summary={
      "from":DATE_FROM,"to":DATE_TO,"matched_days":len(rows),
      "positive_funding_minus_ruonia_days":len(pos),
      "positive_day_pct":(100*len(pos)/len(rows) if rows else None),
      "median_funding_annualized_pct":(statistics.median(r["funding_annualized_pct"] for r in rows) if rows else None),
      "median_excess_vs_ruonia_pct":(statistics.median(xs) if xs else None),
      "max_excess_vs_ruonia_pct":(max(xs) if xs else None),
      "positive_runs":len(runout),
      "runs_net_positive_after_fixed_cost":sum(x["net_after_fixed_cost_rub"]>0 for x in runout),
      "best_run":max(runout,key=lambda x:x["net_after_fixed_cost_rub"]) if runout else None,
      "important_limitations":[
        "Daily settlement/close data, not intraday executable bid/ask replay.",
        "RUONIA is a wholesale overnight funding benchmark, not the user's executable broker/repo funding rate.",
        "Broker commissions and taxes are excluded. Dividend/tax cash-flow mismatch requires a separate after-tax scenario.",
        "Dividend adjustment on SBERF economically offsets the stock dividend for the long-stock/short-perp hedge, but tax/timing effects need separate modelling."
      ]
    }
    (OUT/"moex_sberf_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":
    main()
