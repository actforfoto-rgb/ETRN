from __future__ import annotations

import csv, json, math, statistics
from datetime import date, datetime
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

PAIRS=[
  {"spot":"SBER","perp":"SBERF","stock_board":"TQBR"},
  {"spot":"GAZP","perp":"GAZPF","stock_board":"TQBR"},
]
FROM="2026-01-01"
TILL="2026-09-29"
MARGIN_CAPITAL_RATIO=0.18
ROUNDTRIP_COST_BUFFER_BPS=12.0

KEY_REGIMES=[
 ("2026-01-01",16.00),
 ("2026-02-16",15.50),
 ("2026-03-23",15.00),
 ("2026-04-27",14.50),
 ("2026-06-22",14.25),
 ("2026-07-27",14.00),
]

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=25);r.raise_for_status();return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,r)) for r in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def hist(secid,engine,market,board):
    url=f"{BASE}/history/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
    out=[];start=0
    while True:
      j=get(url,{"from":FROM,"till":TILL,"start":start,"iss.meta":"off"})
      rr=rows(j,"history");out.extend(rr)
      cur=rows(j,"history.cursor")
      total=int(cur[0].get("TOTAL") or 0) if cur else len(out)
      if not rr or len(out)>=total:break
      start=len(out)
    return out

def dividends(secid):
    try:
      j=get(f"{BASE}/securities/{secid}/dividends.json",{"iss.meta":"off"})
      rr=rows(j,"dividends")
      return [{"date":r.get("registryclosedate"),"value":num(r.get("value"))} for r in rr
              if r.get("registryclosedate") and FROM<=r.get("registryclosedate")<=TILL]
    except Exception:return []

def rate_on(d):
    r=16.0
    for start,val in KEY_REGIMES:
      if d>=start:r=val
    return r

def integrate_key_cost_bps(d0,d1,capital_multiplier=1.0):
    a=date.fromisoformat(d0);b=date.fromisoformat(d1)
    cur=a;cost=0.0
    from datetime import timedelta
    while cur<b:
      ds=cur.isoformat()
      cost += rate_on(ds)/365.0*100.0*capital_multiplier
      cur += timedelta(days=1)
    return cost

def price(r,kind):
    names = {
      "spot":["CLOSE","WAPRICE","LEGALCLOSEPRICE"],
      "perp":["CLOSE","WAPRICE","SETTLEPRICE"],
    }[kind]
    for k in names:
      v=num(r.get(k))
      if v is not None:return v
    return None

def analyze_pair(p):
    sh=hist(p["spot"],"stock","shares",p["stock_board"])
    ph=hist(p["perp"],"futures","forts","RFUD")
    sm={r.get("TRADEDATE"):r for r in sh}
    pm={r.get("TRADEDATE"):r for r in ph}
    common=sorted(set(sm)&set(pm))
    divs=dividends(p["spot"])
    divdates={x["date"] for x in divs}
    daily=[]
    for d in common:
      s=sm[d];q=pm[d]
      sp=price(s,"spot");fp=price(q,"perp");swap=num(q.get("SWAPRATE") or q.get("SWAPRATE_CURR"))
      settle=num(q.get("SETTLEPRICE"))
      if sp is None or fp is None or swap is None:continue
      daily.append({
        "date":d,"spot":sp,"perp_market":fp,"perp_settle":settle,
        "basis_bps":(fp/sp-1)*10000,"swap_rub":swap,"swap_bps":swap/sp*10000,
        "perp_volume":num(q.get("VOLUME")) or 0,"perp_trades":num(q.get("NUMTRADES")) or 0,
        "perp_oi":num(q.get("OPENPOSITION")) or 0
      })
    if not daily:return None

    with (OUT/f"moex_{p['perp'].lower()}_daily.csv").open("w",newline="",encoding="utf-8") as f:
      w=csv.DictWriter(f,fieldnames=list(daily[0].keys()));w.writeheader();w.writerows(daily)

    windows=[]
    for n in (3,5,10,20,40):
      for i in range(0,len(daily)-n):
        e=i+n
        a=daily[i];b=daily[e]
        # Funding earned after entry through exit day.
        funding_rub=sum(x["swap_rub"] for x in daily[i+1:e+1])
        basis_pnl_rub=(b["spot"]-a["spot"])+(a["perp_market"]-b["perp_market"])
        gross_bps=(basis_pnl_rub+funding_rub)/a["spot"]*10000
        capital_cost=integrate_key_cost_bps(a["date"],b["date"],1.0+MARGIN_CAPITAL_RATIO)
        crosses_div=any(a["date"]<=d<=b["date"] for d in divdates)
        net=gross_bps-capital_cost-ROUNDTRIP_COST_BUFFER_BPS
        windows.append({
          "pair":p["spot"]+"-"+p["perp"],"n_obs":n,"start":a["date"],"end":b["date"],
          "calendar_days":(date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days,
          "entry_basis_bps":a["basis_bps"],"exit_basis_bps":b["basis_bps"],
          "funding_income_bps":funding_rub/a["spot"]*10000,
          "basis_pnl_bps":basis_pnl_rub/a["spot"]*10000,
          "gross_strategy_bps":gross_bps,"capital_cost_bps":capital_cost,
          "roundtrip_buffer_bps":ROUNDTRIP_COST_BUFFER_BPS,"screen_net_bps":net,
          "crosses_dividend_event":crosses_div
        })

    clean=[x for x in windows if not x["crosses_dividend_event"]]
    clean.sort(key=lambda x:x["screen_net_bps"],reverse=True)
    with (OUT/f"moex_{p['perp'].lower()}_windows.csv").open("w",newline="",encoding="utf-8") as f:
      w=csv.DictWriter(f,fieldnames=list(clean[0].keys()));w.writeheader();w.writerows(clean)

    top=clean[:25]
    summary={
      "pair":p["spot"]+"-"+p["perp"],"daily_rows":len(daily),"dividends":divs,
      "mean_swap_bps_trading_day":statistics.fmean(x["swap_bps"] for x in daily),
      "median_swap_bps_trading_day":statistics.median(x["swap_bps"] for x in daily),
      "positive_clean_windows":sum(x["screen_net_bps"]>0 for x in clean),
      "total_clean_windows":len(clean),
      "positive_clean_windows_pct":100*sum(x["screen_net_bps"]>0 for x in clean)/len(clean) if clean else 0,
      "best_windows":top
    }
    (OUT/f"moex_{p['perp'].lower()}_window_summary.json").write_text(
      json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary

def main():
    allsum=[]
    for p in PAIRS:
      try:
        s=analyze_pair(p)
        if s:allsum.append(s)
      except Exception as e:
        allsum.append({"pair":p["spot"]+"-"+p["perp"],"error":f"{type(e).__name__}: {e}"})
    (OUT/"moex_perpetual_backtest_summary.json").write_text(
      json.dumps(allsum,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(allsum,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
