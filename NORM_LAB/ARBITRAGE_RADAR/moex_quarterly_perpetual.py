from __future__ import annotations

import csv, json, math
from datetime import date, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"

PAIRS=[
  {"spot":"SBER","perp":"SBERF","quarters":["SRZ6","SRH7","SRM7"]},
  {"spot":"GAZP","perp":"GAZPF","quarters":["GZZ6","GZH7","GZM7"]},
]
KEY_RATE=0.14
EXEC_BUFFER_RUB_PER_CONTRACT=10.0

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

def fut(secid):
    j=get(f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s or not m:return None
    a=s[0];b=m[0]
    return {
      "secid":secid,"shortname":a.get("SHORTNAME"),
      "bid":num(b.get("BID")),"ask":num(b.get("OFFER")),
      "last":num(b.get("LAST")),"settle":num(b.get("SETTLEPRICE")),
      "swaprate":num(b.get("SWAPRATE") or b.get("SWAPRATE_CURR")),
      "lot":num(a.get("LOTVOLUME")) or 1.0,
      "im":num(a.get("INITIALMARGIN")) or 0.0,
      "buysellfee":num(a.get("BUYSELLFEE")) or 0.0,
      "scalperfee":num(a.get("SCALPERFEE")) or 0.0,
      "expiry":a.get("LASTTRADEDATE"),
      "volume":num(b.get("VOLTODAY")) or 0.0,
      "trades":num(b.get("NUMTRADES")) or 0.0,
      "oi":num(b.get("OPENPOSITION")) or 0.0,
    }

def business_days(d0,d1):
    n=0;cur=d0
    while cur<d1:
      cur += timedelta(days=1)
      if cur.weekday()<5:n+=1
    return n

def main():
    out=[]
    for cfg in PAIRS:
      p=fut(cfg["perp"])
      if not p or p["bid"] is None or p["swaprate"] is None:continue
      for qid in cfg["quarters"]:
        q=fut(qid)
        if not q or q["ask"] is None or not q["expiry"]:continue
        exp=date.fromisoformat(q["expiry"])
        today=date(2026,9,29)
        if exp<=today:continue
        bdays=business_days(today,exp)
        lot=q["lot"]
        qask_share=q["ask"]/lot
        p_bid_share=p["bid"] # perpetual quoted per share
        convergence_loss=(qask_share-p_bid_share)*lot

        # Long quarterly + short perpetual:
        # at convergence the initial price gap is paid, while short perp receives positive SwapRate.
        funding_current=p["swaprate"]*lot*bdays
        total_im=q["im"]+p["im"]
        margin_carry=total_im*KEY_RATE*((exp-today).days/365.0)
        # Exchange fee approximated by opening+closing each leg using BUYSELLFEE.
        exchange_rt=2*(q["buysellfee"]+p["buysellfee"])
        costs=margin_carry+exchange_rt+EXEC_BUFFER_RUB_PER_CONTRACT
        net_current=funding_current-convergence_loss-costs

        # Break-even average funding per trading day in rub/share.
        required=(convergence_loss+costs)/(lot*bdays) if bdays else None
        out.append({
          "underlying":cfg["spot"],"perpetual":cfg["perp"],"quarterly":qid,
          "expiry":q["expiry"],"calendar_days":(exp-today).days,"business_days":bdays,
          "perp_bid":p["bid"],"perp_swaprate_rub_share_day":p["swaprate"],
          "quarterly_ask":q["ask"],"quarterly_ask_per_share":qask_share,
          "initial_spread_rub_contract":convergence_loss,
          "funding_if_current_rate_rub":funding_current,
          "quarterly_im":q["im"],"perp_im":p["im"],"total_im":total_im,
          "margin_opportunity_cost_rub_key14":margin_carry,
          "exchange_roundtrip_rub":exchange_rt,
          "execution_buffer_rub":EXEC_BUFFER_RUB_PER_CONTRACT,
          "required_avg_swaprate_rub_share_trading_day":required,
          "current_swaprate_over_required_ratio":(p["swaprate"]/required if required and required>0 else None),
          "screen_net_if_current_swaprate_rub":net_current,
          "screen_return_on_total_im_pct":(net_current/total_im*100 if total_im else None),
          "quarterly_volume":q["volume"],"quarterly_oi":q["oi"],
          "perp_volume":p["volume"],"perp_oi":p["oi"],
        })

    out.sort(key=lambda x:x["screen_return_on_total_im_pct"] if x["screen_return_on_total_im_pct"] is not None else -999,reverse=True)
    with (OUT/"moex_quarterly_perpetual_screen.csv").open("w",newline="",encoding="utf-8") as f:
      if out:
        w=csv.DictWriter(f,fieldnames=list(out[0].keys()));w.writeheader();w.writerows(out)
    (OUT/"moex_quarterly_perpetual_top.json").write_text(
      json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
