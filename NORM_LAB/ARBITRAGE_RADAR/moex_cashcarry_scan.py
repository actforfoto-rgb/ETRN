from __future__ import annotations

import csv, json, math
from datetime import date, datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"
KEY_RATE=0.14
EXEC_BUFFER_BPS=5.0
STOCK_FEE_BUFFER_BPS=5.0

# Corporate actions already approved/recommended and known to the market by 2026-09-29.
# Keep both gross-fair-value and a separate 13% dividend-tax scenario; do not mix them.
KNOWN_DIVIDENDS = {
    "TATN":[{"date":"2026-10-13","value":32.88,"status":"approved"}],
    "TATNP":[{"date":"2026-10-13","value":32.88,"status":"approved"}],
    "GMKN":[{"date":"2026-10-12","value":1.85,"status":"recommended"}],
}

ASSET_TO_SPOT = {
"SBRF":"SBER","SBPR":"SBERP","GAZR":"GAZP","GMKN":"GMKN","LKOH":"LKOH","ROSN":"ROSN",
"YDEX":"YDEX","VTBR":"VTBR","NOTK":"NVTK","PLZL":"PLZL","FEES":"FEES","HEAD":"HEAD",
"SIBN":"SIBN","SOFL":"SOFL","AFLT":"AFLT","AFKS":"AFKS","TATN":"TATN","TATP":"TATNP",
"RTKM":"RTKM","RTKMP":"RTKMP","MGNT":"MGNT","MTSS":"MTSS","MOEX":"MOEX","NLMK":"NLMK",
"CHMF":"CHMF","ALRS":"ALRS","PHOR":"PHOR","RUAL":"RUAL","IRAO":"IRAO","PIKK":"PIKK",
"MAGN":"MAGN","FLOT":"FLOT","HYDR":"HYDR","TRNF":"TRNFP",
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

def spot_snapshot(secid):
    j=get(f"{BASE}/engines/stock/markets/shares/boards/TQBR/securities/{secid}.json",{"iss.meta":"off"})
    s=rows(j,"securities");m=rows(j,"marketdata")
    if not s or not m:return None
    sr=s[0];mr=m[0]
    return {
      "ask":num(mr.get("OFFER")),"bid":num(mr.get("BID")),"last":num(mr.get("LAST")),
      "lot":num(sr.get("LOTSIZE")) or 1,
      "volume":num(mr.get("VOLTODAY")) or 0,"value":num(mr.get("VALTODAY")) or 0,
      "trades":num(mr.get("NUMTRADES")) or 0
    }

def dividends(secid, today, expiry):
    # First use the explicit verified/recommended corporate-action registry.
    explicit=[x for x in KNOWN_DIVIDENDS.get(secid,[]) if today < x["date"] <= expiry]
    if explicit:
        return explicit,sum(float(x["value"]) for x in explicit)
    try:
        j=get(f"{BASE}/securities/{secid}/dividends.json",{"iss.meta":"off"})
        rr=rows(j,"dividends")
    except Exception:
        return [],0.0
    keep=[];total=0.0
    for r in rr:
        d=r.get("registryclosedate") or r.get("registry_close_date")
        val=num(r.get("value"))
        if d and val is not None and today < d <= expiry:
            keep.append({"date":d,"value":val,"status":"iss"})
            total += val
    return keep,total

def main():
    today=date(2026,9,29)
    j=get(f"{BASE}/engines/futures/markets/forts/securities.json",{
      "iss.meta":"off","iss.only":"securities,marketdata"
    })
    secs=rows(j,"securities")
    md={r.get("SECID"):r for r in rows(j,"marketdata")}
    spot_cache={}
    out=[]

    for s in secs:
        asset=s.get("ASSETCODE")
        spot=ASSET_TO_SPOT.get(asset)
        secid=s.get("SECID")
        if not spot or not secid or secid in ("SBERF","GAZPF"):
            continue
        exp=s.get("LASTTRADEDATE")
        try: expd=date.fromisoformat(exp)
        except: continue
        if expd<=today: continue
        m=md.get(secid,{})
        fb=num(m.get("BID"))
        fa=num(m.get("OFFER"))
        if fb is None or fa is None: continue
        lot=num(s.get("LOTVOLUME")) or 1.0
        fut_bid_per_share=fb/lot
        if spot not in spot_cache:
            spot_cache[spot]=spot_snapshot(spot)
        sp=spot_cache[spot]
        if not sp or sp.get("ask") is None: continue
        S0=sp["ask"]
        T=(expd-today).days/365.0
        divs,div_total=dividends(spot,today.isoformat(),exp)
        # Known cash dividends only. Unknown future dividends remain model risk.
        fair=(S0-div_total)*math.exp(KEY_RATE*T)
        observed=fut_bid_per_share
        raw_basis_bps=(observed/S0-1)*10000
        fair_basis_bps=(fair/S0-1)*10000
        futures_fee=num(s.get("BUYSELLFEE")) or 0.0
        futures_rt_bps=(2*futures_fee/(S0*lot))*10000
        net_excess_bps=raw_basis_bps-fair_basis_bps-futures_rt_bps-EXEC_BUFFER_BPS-STOCK_FEE_BUFFER_BPS
        ann_excess_pct=(net_excess_bps/100)/T if T>0 else None

        # Scenario for a taxable holder receiving only 87% of the cash dividend.
        # The futures market typically reflects gross corporate action economics, so this is
        # an important retail-screening drag rather than a fair-value input for the contract.
        net_dividend_13 = div_total * 0.87
        investor_fair_13=(S0-net_dividend_13)*math.exp(KEY_RATE*T)
        investor_fair_13_bps=(investor_fair_13/S0-1)*10000
        investor_net_13_bps=raw_basis_bps-investor_fair_13_bps-futures_rt_bps-EXEC_BUFFER_BPS-STOCK_FEE_BUFFER_BPS
        out.append({
          "spot":spot,"assetcode":asset,"future":secid,"shortname":s.get("SHORTNAME"),
          "expiry":exp,"days":(expd-today).days,"lotvolume":lot,
          "spot_ask":S0,"future_bid":fb,"future_bid_per_share":fut_bid_per_share,
          "raw_basis_bps":raw_basis_bps,"fair_basis_bps_key14_known_div":fair_basis_bps,
          "known_dividend_rub":div_total,"known_dividend_events":json.dumps(divs,ensure_ascii=False),
          "future_roundtrip_fee_bps":futures_rt_bps,
          "exec_plus_stockfee_buffer_bps":EXEC_BUFFER_BPS+STOCK_FEE_BUFFER_BPS,
          "screen_net_excess_bps":net_excess_bps,
          "screen_net_annualized_pct":ann_excess_pct,
          "screen_net_bps_dividend_tax13_scenario":investor_net_13_bps,
          "future_volume":num(m.get("VOLTODAY")) or 0,
          "future_value":num(m.get("VALTODAY")) or 0,
          "future_numtrades":num(m.get("NUMTRADES")) or 0,
          "openposition":num(m.get("OPENPOSITION")) or 0,
          "initialmargin":num(s.get("INITIALMARGIN")),
          "unknown_dividend_risk": "YES" if div_total==0 and (expd-today).days>120 else "LOWER"
        })

    out.sort(key=lambda r:(r["screen_net_excess_bps"],r["future_value"]),reverse=True)
    p=OUT/"moex_cashcarry_screen.csv"
    if out:
      with p.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(out[0].keys()));w.writeheader();w.writerows(out)
    top=out[:30]
    (OUT/"moex_cashcarry_top.json").write_text(json.dumps(top,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(top[:10],ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
