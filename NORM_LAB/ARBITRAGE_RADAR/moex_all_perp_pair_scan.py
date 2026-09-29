from __future__ import annotations

import csv, json, math
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({"User-Agent":"NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE="https://iss.moex.com/iss"
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()
KEY_RATE=0.14
MIN_PERP_TRADES=5
MIN_QUARTER_TRADES=5

def get(url,params=None):
    r=S.get(url,params=params or {},timeout=30); r.raise_for_status(); return r.json()

def rows(j,name):
    b=j.get(name,{})
    cols=b.get("columns",[])
    return [dict(zip(cols,x)) for x in b.get("data",[])]

def num(v):
    try:return float(v)
    except:return None

def bdays(a,b):
    n=0; d=a
    while d<b:
        d += timedelta(days=1)
        if d.weekday()<5:n+=1
    return n

def infer_scale(qmid,pmid):
    cands=[0.0001,0.001,0.01,0.1,1,10,100,1000,10000]
    return min(cands,key=lambda x:abs(math.log(max(1e-12,(qmid/x)/pmid))))

def unit_rub(s):
    ms=num(s.get("MINSTEP")) or 1.0
    sp=num(s.get("STEPPRICE")) or ms
    return sp/ms

def main():
    j=get(f"{BASE}/engines/futures/markets/forts/securities.json",{
      "iss.meta":"off","iss.only":"securities,marketdata"
    })
    securities=rows(j,"securities")
    md={x.get("SECID"):x for x in rows(j,"marketdata")}

    # Detect perpetuals structurally: 2100 expiration, non-empty SwapRate, real market activity.
    perps=[]
    fixed=[]
    for s in securities:
        sid=s.get("SECID")
        if not sid:continue
        exp=s.get("LASTTRADEDATE")
        m=md.get(sid,{})
        sw=num(m.get("SWAPRATE") or m.get("SWAPRATE_CURR"))
        try: ed=date.fromisoformat(exp)
        except: ed=None
        row={"s":s,"m":m,"sid":sid,"expiry":ed,"swap":sw}
        if ed and ed.year>=2099 and sw is not None:
            perps.append(row)
        elif ed and TODAY<ed and ed.year<2099:
            fixed.append(row)

    out=[]
    unmatched=[]
    for p in perps:
        ps=p["s"]; pm=p["m"]; asset=ps.get("ASSETCODE")
        pb=num(pm.get("BID")); pa=num(pm.get("OFFER"))
        ptr=num(pm.get("NUMTRADES")) or 0
        if pb is None or pa is None or ptr<MIN_PERP_TRADES:continue

        # Prefer exact ASSETCODE; if missing, no speculative name matching.
        candidates=[q for q in fixed if asset and q["s"].get("ASSETCODE")==asset]
        # current bid/ask + minimum activity
        candidates=[q for q in candidates
                    if num(q["m"].get("BID")) is not None and num(q["m"].get("OFFER")) is not None
                    and (num(q["m"].get("NUMTRADES")) or 0)>=MIN_QUARTER_TRADES]
        if not candidates:
            unmatched.append({"perp":p["sid"],"assetcode":asset,"swaprate":p["swap"],
                              "perp_trades":ptr,"reason":"no liquid same-ASSETCODE fixed-expiry future"})
            continue
        candidates.sort(key=lambda q:q["expiry"])
        q=candidates[0]; qs=q["s"]; qm=q["m"]
        qb=num(qm.get("BID"));qa=num(qm.get("OFFER"))
        qmid=(qb+qa)/2;pmid=(pb+pa)/2
        scale=infer_scale(qmid,pmid)
        qu=unit_rub(qs);pu=unit_rub(ps)
        qsens=qu*scale;psens=pu
        qqty=psens/qsens
        days=(q["expiry"]-TODAY).days
        work=bdays(TODAY,q["expiry"])

        swap=p["swap"]
        if swap>=0:
            direction="LONG_FIXED_SHORT_PERP"
            conv=pb*pu-qa*qqty*qu
        else:
            direction="SHORT_FIXED_LONG_PERP"
            conv=qb*qqty*qu-pa*pu
        fund=abs(swap)*pu*work
        qim=num(qs.get("INITIALMARGIN")) or 0.0
        pim=num(ps.get("INITIALMARGIN")) or 0.0
        margin=qqty*qim+pim
        carry=margin*KEY_RATE*days/365
        qfee=num(qs.get("BUYSELLFEE")) or 0.0
        pfee=num(ps.get("BUYSELLFEE")) or 0.0
        fees=2*(qqty*qfee+pfee)
        # current full-spread buffer for exit
        spreadbuf=(qa-qb)*qqty*qu+(pa-pb)*pu
        net=conv+fund-carry-fees-spreadbuf
        required=(carry+fees+spreadbuf-conv)/(pu*work) if work and pu else None

        out.append({
          "assetcode":asset,"perp":p["sid"],"perp_shortname":ps.get("SHORTNAME"),
          "fixed":q["sid"],"fixed_shortname":qs.get("SHORTNAME"),
          "direction":direction,"expiry":q["expiry"].isoformat(),"calendar_days":days,"business_days":work,
          "perp_bid":pb,"perp_ask":pa,"fixed_bid":qb,"fixed_ask":qa,
          "fixed_scale_to_perp":scale,"fixed_qty_per_1_perp":qqty,
          "perp_unit_rub":pu,"fixed_unit_rub":qu,
          "swaprate":swap,"funding_if_current_rate_rub":fund,
          "convergence_pnl_rub":conv,"combined_im_rub":margin,"im_carry_key14_rub":carry,
          "roundtrip_exchange_fee_rub":fees,"exit_full_spread_buffer_rub":spreadbuf,
          "screen_net_rub":net,"screen_return_on_im_pct":net/margin*100 if margin else None,
          "required_abs_swaprate":required,
          "current_swaprate_over_required":abs(swap)/required if required and required>0 else None,
          "perp_trades":ptr,"perp_volume":num(pm.get("VOLTODAY")) or 0,
          "perp_oi":num(pm.get("OPENPOSITION")) or 0,
          "fixed_trades":num(qm.get("NUMTRADES")) or 0,
          "fixed_volume":num(qm.get("VOLTODAY")) or 0,
          "fixed_oi":num(qm.get("OPENPOSITION")) or 0,
        })

    out.sort(key=lambda x:x["screen_return_on_im_pct"] if x["screen_return_on_im_pct"] is not None else -999,reverse=True)
    (OUT/"moex_all_perp_pair_top.json").write_text(json.dumps(out[:100],ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"moex_all_perp_unmatched.json").write_text(json.dumps(unmatched,ensure_ascii=False,indent=2),encoding="utf-8")
    if out:
        with (OUT/"moex_all_perp_pair_screen.csv").open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(out[0].keys()));w.writeheader();w.writerows(out)
    summary={
      "perpetuals_detected":len(perps),
      "paired_liquid_perpetuals":len(out),
      "positive_current_screen":sum(x["screen_net_rub"]>0 for x in out),
      "top":out[:20]
    }
    (OUT/"moex_all_perp_pair_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
