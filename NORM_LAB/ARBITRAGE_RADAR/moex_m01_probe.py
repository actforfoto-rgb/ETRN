from __future__ import annotations

import csv, json, math, time
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session()
S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.1"})
ISS="https://iss.moex.com/iss"

TARGET_SHORTNAMES=["SBRF-12.26","SBRF-3.27","SBRF-6.27"]
FUNDING_SCENARIOS=[0.1413,0.16,0.18]  # RUONIA-like baseline + stress scenarios
SPOT_TAKER_EXCHANGE=0.0003
EXECUTION_BUFFER_BPS=5.0

def utc(): return datetime.now(timezone.utc).isoformat()

def get(url, timeout=25):
    r=S.get(url,timeout=timeout)
    r.raise_for_status()
    return r.json()

def block(j,name):
    x=j.get(name,{})
    return [dict(zip(x.get("columns",[]),r)) for r in x.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def field(row,*names):
    for n in names:
        if row.get(n) not in (None,""): return row[n]
    return None

def discover_forts():
    rows=[]
    start=0
    while True:
        cols="SECID,SHORTNAME,ASSETCODE,LASTTRADEDATE,LOTVOLUME,INITIALMARGIN,BUYSELLFEE,SCALPERFEE,SETTLEPRICE_CLR"
        url=f"{ISS}/engines/futures/markets/forts/securities.json?iss.meta=off&iss.only=securities&securities.columns={cols}&start={start}"
        j=get(url)
        page=block(j,"securities")
        rows.extend(page)
        if len(page)<100: break
        start += len(page)
        if start>3000: break
    found={}
    for r in rows:
        sn=str(r.get("SHORTNAME") or "")
        if sn in TARGET_SHORTNAMES:
            found[sn]=r
    (OUT/"moex_m01_forts_catalog.json").write_text(json.dumps(
        {"targets":found,"total_rows":len(rows)},ensure_ascii=False,indent=2),encoding="utf-8")
    return found

def market(secid):
    j=get(f"{ISS}/engines/futures/markets/forts/securities/{secid}.json?iss.meta=off")
    sec=block(j,"securities")
    md=block(j,"marketdata")
    return (sec[0] if sec else {}, md[0] if md else {})

def stock():
    j=get(f"{ISS}/engines/stock/markets/shares/boards/TQBR/securities/SBER.json?iss.meta=off")
    sec=block(j,"securities")[0]
    md=block(j,"marketdata")[0]
    return sec,md

def history(secid, date_from="2026-03-01", date_to="2026-09-29"):
    allrows=[];start=0
    while True:
        u=f"{ISS}/history/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json?from={date_from}&till={date_to}&iss.meta=off&start={start}"
        j=get(u)
        pg=block(j,"history");allrows.extend(pg)
        if len(pg)<100:break
        start += len(pg)
    return allrows

def px(row,side):
    if side=="bid": return num(field(row,"BID","BESTBID"))
    if side=="ask": return num(field(row,"OFFER","BESTASK"))
    return num(field(row,"LAST","SETTLEPRICE","SETTLEPRICE_CLR"))

def main():
    catalog=discover_forts()
    sec_s,md_s=stock()
    s_bid=px(md_s,"bid");s_ask=px(md_s,"ask");s_last=px(md_s,"last")
    stock_row={
        "utc":utc(),"instrument":"SBER","secid":"SBER","bid":s_bid,"ask":s_ask,"last":s_last,
        "lot_volume":sec_s.get("LOTSIZE"),"initial_margin":None,"buy_sell_fee":None,
        "expiry":None,"settle":None,"open_interest":None,"volume":md_s.get("VOLTODAY"),
        "numtrades":md_s.get("NUMTRADES"),"systime":md_s.get("SYSTIME")
    }
    rows=[stock_row]; diags=[]; histories={}
    nowdt=datetime.now(timezone.utc)

    # perpetual, discovered directly
    psec,pmd=market("SBERF")
    rows.append({
        "utc":utc(),"instrument":"SBERF","secid":"SBERF","bid":px(pmd,"bid"),"ask":px(pmd,"ask"),
        "last":px(pmd,"last"),"lot_volume":psec.get("LOTVOLUME"),"initial_margin":psec.get("INITIALMARGIN"),
        "buy_sell_fee":psec.get("BUYSELLFEE"),"expiry":psec.get("LASTTRADEDATE"),
        "settle":field(pmd,"SETTLEPRICE","SETTLEPRICE_CLR"),"open_interest":pmd.get("OPENPOSITION"),
        "volume":pmd.get("VOLTODAY"),"numtrades":pmd.get("NUMTRADES"),"systime":pmd.get("SYSTIME")
    })

    for human in TARGET_SHORTNAMES:
        ref=catalog.get(human)
        if not ref: continue
        secid=ref["SECID"]
        sec,md=market(secid)
        lot=float(sec.get("LOTVOLUME") or ref.get("LOTVOLUME") or 100)
        bid=px(md,"bid");ask=px(md,"ask");last=px(md,"last")
        exp_s=sec.get("LASTTRADEDATE") or ref.get("LASTTRADEDATE")
        rows.append({
            "utc":utc(),"instrument":human,"secid":secid,"bid":bid,"ask":ask,"last":last,
            "lot_volume":lot,"initial_margin":sec.get("INITIALMARGIN") or ref.get("INITIALMARGIN"),
            "buy_sell_fee":sec.get("BUYSELLFEE") or ref.get("BUYSELLFEE"),
            "expiry":exp_s,"settle":field(md,"SETTLEPRICE","SETTLEPRICE_CLR"),
            "open_interest":md.get("OPENPOSITION"),"volume":md.get("VOLTODAY"),
            "numtrades":md.get("NUMTRADES"),"systime":md.get("SYSTIME")
        })
        histories[human]=history(secid)
        if s_ask and bid and exp_s:
            exp=datetime.fromisoformat(exp_s).replace(tzinfo=timezone.utc)
            T=max((exp-nowdt).total_seconds(),0)/(365.0*86400)
            fut_share=bid/lot
            gross=(fut_share/s_ask-1.0)
            fut_fee=float(sec.get("BUYSELLFEE") or ref.get("BUYSELLFEE") or 0.0)
            # 100-share cash-and-carry; exchange-only estimate, no broker fee.
            spot_exchange_roundtrip = 2*SPOT_TAKER_EXCHANGE*(s_ask*lot)
            futures_roundtrip = 2*fut_fee
            execution_buffer_rub = EXECUTION_BUFFER_BPS/10000.0*(s_ask*lot)
            for rate in FUNDING_SCENARIOS:
                fair=s_ask*math.exp(rate*T)
                fair_basis=fair/s_ask-1.0
                excess_before_cost=gross-fair_basis
                gross_excess_rub=excess_before_cost*s_ask*lot
                net_rub=gross_excess_rub-spot_exchange_roundtrip-futures_roundtrip-execution_buffer_rub
                capital=s_ask*lot
                net_pct=net_rub/capital if capital else None
                annualized=(net_pct/T if T>0 else None)
                diags.append({
                    "utc":utc(),"instrument":human,"secid":secid,"spot_ask":s_ask,
                    "futures_bid":bid,"futures_bid_per_share":fut_share,
                    "lot":lot,"days_to_expiry":round(T*365,3),
                    "funding_rate_scenario":rate,
                    "raw_basis_pct":gross*100,"fair_basis_no_div_pct":fair_basis*100,
                    "excess_basis_before_cost_pct":excess_before_cost*100,
                    "spot_exchange_roundtrip_rub":spot_exchange_roundtrip,
                    "futures_roundtrip_fee_rub":futures_roundtrip,
                    "execution_buffer_rub":execution_buffer_rub,
                    "net_excess_rub_before_broker_dividend":net_rub,
                    "net_excess_pct_before_broker_dividend":None if net_pct is None else net_pct*100,
                    "simple_annualized_net_pct":None if annualized is None else annualized*100
                })

    with (OUT/"moex_m01_current.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)
    if diags:
        with (OUT/"moex_m01_current_diagnostic.csv").open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(diags[0].keys()));w.writeheader();w.writerows(diags)
    (OUT/"moex_m01_history_probe.json").write_text(json.dumps({
        k:{"rows":len(v),"sample":v[:2]} for k,v in histories.items()
    },ensure_ascii=False,indent=2),encoding="utf-8")

if __name__=="__main__":
    main()
