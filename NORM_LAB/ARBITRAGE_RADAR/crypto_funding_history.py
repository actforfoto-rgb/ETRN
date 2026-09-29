from __future__ import annotations

import csv, json, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

VENUES=["okx","bitget","gate","mexc","htx"]
ASSETS=["OP","ETH","DOGE","ADA","ARB","SOL","XRP","BTC"]
FEE={"OKX":0.0005,"BITGET":0.0006,"GATE":0.0005,"MEXC":0.0008,"HTX":0.0005}
DAYS=14

def ts_date(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    pairs={}
    for b in ASSETS:
        ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear") and
            m.get("active",True) and m.get("base")==b and m.get("quote")=="USDT"]
        if ms:pairs[b]=ms[0]["symbol"]
    return exid.upper(),ex,pairs

def history(ex,symbol):
    since=int((time.time()-DAYS*86400)*1000)
    if not ex.has.get("fetchFundingRateHistory"):
        return []
    out=[]
    cursor=since
    for _ in range(5):
        try:
            rr=ex.fetch_funding_rate_history(symbol,cursor,200)
        except Exception:
            break
        if not rr:break
        out.extend(rr)
        mx=max(int(x.get("timestamp") or 0) for x in rr)
        if mx<=cursor:break
        cursor=mx+1
        if cursor>=int(time.time()*1000)-60000:break
    seen={}
    for x in out:
        t=int(x.get("timestamp") or 0)
        r=x.get("fundingRate")
        if t and r is not None:seen[t]=float(r)
    return [{"ts":t,"date":ts_date(t),"rate":r} for t,r in sorted(seen.items())]

def daily(hist):
    d={}
    for x in hist:
        d[x["date"]]=d.get(x["date"],0.0)+x["rate"]*10000
    return d

def main():
    provider={}
    errors=[]
    raw={}
    for exid in VENUES:
        try:
            name,ex,pairs=build(exid)
            provider[name]=(ex,pairs)
            for b,sym in pairs.items():
                try:
                    h=history(ex,sym)
                    raw[f"{name}|{b}"]=h
                except Exception as e:
                    errors.append({"provider":name,"base":b,"error":f"{type(e).__name__}: {e}"[:600]})
        except Exception as e:
            errors.append({"provider":exid.upper(),"base":"*","error":f"{type(e).__name__}: {e}"[:600]})

    (OUT/"crypto_funding_history_raw.json").write_text(json.dumps(raw,ensure_ascii=False),encoding="utf-8")

    summaries=[]
    daily_map={k:daily(v) for k,v in raw.items()}
    for b in ASSETS:
        venues=[k.split("|")[0] for k in daily_map if k.endswith("|"+b)]
        for ln in venues:
            for sn in venues:
                if ln==sn:continue
                L=daily_map.get(f"{ln}|{b}",{})
                S=daily_map.get(f"{sn}|{b}",{})
                dates=sorted(set(L)&set(S))
                if len(dates)<3:continue
                captures=[-L[d]+S[d] for d in dates]
                cum=sum(captures)
                fee_bps=2*(FEE.get(ln,0.0007)+FEE.get(sn,0.0007))*10000
                # Funding-only persistence test: how many rolling 3/5/7-day windows cover fees.
                row={
                    "base":b,"long_provider":ln,"short_provider":sn,
                    "overlap_days":len(dates),"funding_capture_total_bps":cum,
                    "mean_capture_bps_day":statistics.fmean(captures),
                    "median_capture_bps_day":statistics.median(captures),
                    "positive_days_pct":100*sum(x>0 for x in captures)/len(captures),
                    "roundtrip_fee_bps":fee_bps,
                    "funding_only_net_14d_bps":cum-fee_bps,
                    "first_date":dates[0],"last_date":dates[-1],
                }
                for n in (3,5,7):
                    vals=[]
                    for i in range(len(dates)-n+1):
                        vals.append(sum(captures[i:i+n])-fee_bps)
                    row[f"best_{n}d_net_bps"]=max(vals) if vals else None
                    row[f"positive_{n}d_windows_pct"]=(100*sum(x>0 for x in vals)/len(vals)) if vals else None
                summaries.append(row)

    summaries.sort(key=lambda r:r["funding_only_net_14d_bps"],reverse=True)
    if summaries:
        with (OUT/"crypto_funding_history_summary.csv").open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(summaries[0].keys()));w.writeheader();w.writerows(summaries)
    (OUT/"crypto_funding_history_top.json").write_text(
        json.dumps(summaries[:50],ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"crypto_funding_history_errors.json").write_text(
        json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summaries[:20],ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
