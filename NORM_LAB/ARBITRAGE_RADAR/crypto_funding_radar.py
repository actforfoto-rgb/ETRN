from __future__ import annotations

import csv, json, math, statistics, time
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

VENUES=["okx","bitget","gate","mexc","htx"]
FEE={
 "OKX":0.0005,
 "BITGET":0.0006,
 "GATE":0.0005,
 "MEXC":0.0008,
 "HTX":0.0005,
}
BASES=[
 "BTC","ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH",
 "SUI","TRX","TON","HBAR","PEPE","WIF","APT","ARB","OP","UNI",
 "DOT","NEAR","FIL","AAVE","ATOM","ETC","ICP","INJ","XLM","SHIB"
]
CAPITALS=[1000.0,10000.0]

def vwap(levels,quote):
    rem=quote;base=done=0.0
    for x in levels:
        px=float(x[0]);qty=float(x[1])
        q=px*qty;take=min(rem,q)
        done+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None
    return done/base

def build(exid):
    cls=getattr(ccxt,exid)
    ex=cls({"enableRateLimit":True,"timeout":15000})
    ex.load_markets()
    pairs={}
    for b in BASES:
        ms=[m for m in ex.markets.values() if m.get("swap") and m.get("linear")
            and m.get("active",True) and m.get("base")==b and m.get("quote")=="USDT"]
        if ms:pairs[b]=ms[0]
    return exid.upper(),ex,pairs

def funding(ex,symbol):
    try:
      if ex.has.get("fetchFundingRate"):
        r=ex.fetch_funding_rate(symbol)
        return float(r.get("fundingRate") or 0.0), r.get("fundingTimestamp"), r.get("interval")
    except Exception:
      pass
    return 0.0,None,None

def main():
    providers={}
    coverage=[]
    for exid in VENUES:
      try:
        name,ex,pairs=build(exid)
        providers[name]=(ex,pairs)
        coverage.append({"provider":name,"ok":True,"markets":len(pairs)})
      except Exception as e:
        coverage.append({"provider":exid.upper(),"ok":False,"error":f"{type(e).__name__}: {e}"[:600]})
    (OUT/"crypto_funding_coverage.json").write_text(json.dumps(coverage,ensure_ascii=False,indent=2),encoding="utf-8")

    snap={}
    errors=[]
    for name,(ex,pairs) in providers.items():
      for b,m in pairs.items():
        try:
          fr,fts,interval=funding(ex,m["symbol"])
          # order book only for assets with nonzero funding; all normal swaps qualify
          limit=20 if name=="HTX" else 50
          ob=ex.fetch_order_book(m["symbol"],limit)
          snap[(name,b)]={
            "funding":fr,"funding_ts":fts,"interval":interval,
            "asks":ob["asks"],"bids":ob["bids"],"symbol":m["symbol"],
            "contract_size":float(m.get("contractSize") or 1.0)
          }
        except Exception as e:
          errors.append({"provider":name,"base":b,"error":f"{type(e).__name__}: {e}"[:700]})

    rows=[]
    for b in BASES:
      items=[(n,d) for (n,x),d in snap.items() if x==b]
      for ln,L in items:
        for sn,S in items:
          if ln==sn:continue
          # long pays positive funding, receives negative; short receives positive.
          fund=(-L["funding"]+S["funding"])*10000
          if fund<=0:continue
          for cap in CAPITALS:
            lp=vwap(L["asks"],cap);sp=vwap(S["bids"],cap)
            if lp is None or sp is None:continue
            gross=(sp/lp-1)*10000
            rt=2*(FEE.get(ln,0.0007)+FEE.get(sn,0.0007))*10000
            one=gross+fund-rt
            be=max(0.0,(rt-gross)/fund) if fund>0 else None
            rows.append({
              "base":b,"capital":cap,"long_provider":ln,"short_provider":sn,
              "long_symbol":L["symbol"],"short_symbol":S["symbol"],
              "long_funding_bps":L["funding"]*10000,
              "short_funding_bps":S["funding"]*10000,
              "funding_capture_bps_per_event":fund,
              "gross_basis_bps":gross,"roundtrip_fee_bps":rt,
              "net_after_1_funding_bps":one,
              "funding_events_to_breakeven":be,
              "net_after_3_funding_bps":gross+3*fund-rt,
              "net_after_6_funding_bps":gross+6*fund-rt
            })
    rows.sort(key=lambda r:(r["net_after_1_funding_bps"],r["funding_capture_bps_per_event"]),reverse=True)
    if rows:
      with (OUT/"crypto_funding_radar.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)
      top=rows[:50]
    else:top=[]
    (OUT/"crypto_funding_top.json").write_text(json.dumps(top,ensure_ascii=False,indent=2),encoding="utf-8")
    (OUT/"crypto_funding_errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(top[:20],ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
