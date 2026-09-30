from __future__ import annotations

import csv, json, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
STATE_DIR=ROOT/"event_state"
STATE_DIR.mkdir(parents=True,exist_ok=True)
LAST=STATE_DIR/"crypto_prefunded_lock_last.json"
LEDGER=STATE_DIR/"crypto_prefunded_lock_opportunities.csv"

VENUES=["okx","bitget","gate","mexc","htx"]
ASSETS=["BTC","ETH","SOL","XRP","DOGE","ADA","LINK","AVAX","LTC","BCH",
        "SUI","HBAR","WIF","APT","ARB","OP","UNI","DOT","FIL","ICP","AAVE","ETC"]
CAPITALS=[100.0,1000.0]

TAKER={"OKX":0.0010,"BITGET":0.0010,"GATE":0.0010,"MEXC":0.0005,"HTX":0.0020}
EXTRA_BUFFER_BPS=5.0
MIN_NET_BPS=2.0

FIELDS=[
 "utc","base","capital_usdt","buy_venue","sell_venue","buy_vwap","sell_vwap",
 "trade_gross_bps","trade_fee_bps","asset_network","asset_transfer_usd",
 "usdt_network","usdt_transfer_usd","net_lock_usd","net_lock_bps","status"
]

ALIASES={
 "ERC20":"ETH","ETHEREUM":"ETH","ETH":"ETH",
 "TRC20":"TRX","TRON":"TRX","TRX":"TRX",
 "BEP20":"BSC","BSC":"BSC","BEP2":"BNB","BNB":"BNB",
 "OPTIMISM":"OPTIMISM","OP":"OPTIMISM",
 "ARBITRUM":"ARBITRUM","ARBITRUMONE":"ARBITRUM","ARB":"ARBITRUM",
 "SOL":"SOL","SOLANA":"SOL",
 "ICP":"ICP","HBAR":"HBAR","APT":"APT","APTOS":"APT",
 "SUI":"SUI","XRP":"XRP","DOGE":"DOGE","LTC":"LTC","BCH":"BCH",
 "AVAXC":"AVAXC","AVALANCHEC":"AVAXC",
}

def utc():return datetime.now(timezone.utc).isoformat()

def normnet(s):
    if not s:return None
    x=str(s).upper().replace("-","").replace("_","").replace(" ","")
    return ALIASES.get(x,x)

def build(exid):
    ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":18000})
    ex.load_markets()
    return exid.upper(),ex

def symbol(ex,base):
    ms=[m for m in ex.markets.values()
        if m.get("spot") and m.get("active",True)
        and m.get("base")==base and m.get("quote")=="USDT"]
    return ms[0]["symbol"] if ms else None

def vwap_buy(asks,quote):
    rem=quote;base=done=0.0
    for x in asks:
        px=float(x[0]);qty=float(x[1]);q=px*qty;take=min(rem,q)
        done+=take;base+=take/px;rem-=take
        if rem<=1e-9:break
    if rem>1e-6 or base<=0:return None,None
    return base,done/base

def vwap_sell(bids,base):
    rem=base;quote=done=0.0
    for x in bids:
        px=float(x[0]);qty=float(x[1]);take=min(rem,qty)
        quote+=take*px;done+=take;rem-=take
        if rem<=1e-12:break
    if rem>1e-9 or done<=0:return None,None
    return quote,quote/done

def networks(ex,currency):
    try:
        cur=(ex.currencies or {}).get(currency)
    except Exception:
        cur=None
    if not cur:return {}
    out={}
    nets=cur.get("networks") or {}
    for raw,n in nets.items():
        key=normnet(n.get("network") or raw)
        if not key:continue
        dep=n.get("deposit")
        wd=n.get("withdraw")
        active=n.get("active")
        fee=n.get("fee")
        # Keep only explicitly usable networks; null active is tolerated if both directions true.
        if dep is True and wd is True and active is not False:
            try:fee=float(fee) if fee is not None else None
            except:fee=None
            out[key]={"fee":fee,"raw":raw}
    return out

def common_transfer(buy_ex,sell_ex,currency):
    # Asset needs low->high: withdrawal on buy venue + deposit on sell venue.
    # CCXT network metadata above requires both directions at each venue, a deliberately
    # conservative condition for inventory rotation.
    A=networks(buy_ex,currency)
    B=networks(sell_ex,currency)
    common=sorted(set(A)&set(B))
    candidates=[]
    for n in common:
        fee=A[n]["fee"]  # withdrawal fee paid at source
        if fee is not None:candidates.append((fee,n))
    if not candidates:return None
    candidates.sort()
    fee,n=candidates[0]
    return {"network":n,"fee":fee}

def scan():
    exs={};coverage=[];books={};symbols={}
    for exid in VENUES:
        try:
            name,ex=build(exid);exs[name]=ex
            coverage.append({"venue":name,"ok":True})
        except Exception as e:
            coverage.append({"venue":exid.upper(),"ok":False,"error":f"{type(e).__name__}: {e}"[:500]})

    for name,ex in exs.items():
        for base in ASSETS:
            s=symbol(ex,base)
            if not s:continue
            try:
                ob=ex.fetch_order_book(s,50 if name!="HTX" else 20)
                if name=="MEXC":time.sleep(0.25)
                books[(name,base)]=ob;symbols[(name,base)]=s
            except Exception:
                pass

    rows=[]
    names=sorted(exs)
    for base in ASSETS:
        for buy in names:
            for sell in names:
                if buy==sell:continue
                bo=books.get((buy,base));so=books.get((sell,base))
                if not bo or not so:continue

                asset_route=common_transfer(exs[buy],exs[sell],base)
                usdt_route=common_transfer(exs[sell],exs[buy],"USDT")
                transfer_verified=asset_route is not None and usdt_route is not None

                for cap in CAPITALS:
                    base_qty,buy_px=vwap_buy(bo.get("asks") or [],cap)
                    if not base_qty:continue
                    # Fee modeled in quote terms for conservatism; sell same gross quantity
                    # requires prefunded inventory on sell venue.
                    proceeds,sell_px=vwap_sell(so.get("bids") or [],base_qty)
                    if proceeds is None:continue

                    gross=proceeds-cap
                    trade_fee=cap*TAKER[buy]+proceeds*TAKER[sell]
                    asset_transfer_usd=None
                    usdt_transfer_usd=None
                    if transfer_verified:
                        asset_transfer_usd=float(asset_route["fee"])*sell_px
                        usdt_transfer_usd=float(usdt_route["fee"])
                        net=gross-trade_fee-asset_transfer_usd-usdt_transfer_usd-cap*EXTRA_BUFFER_BPS/10000
                        net_bps=net/cap*10000
                        status="EXECUTABLE_CANDIDATE" if net_bps>=MIN_NET_BPS else "REJECT"
                    else:
                        net=None;net_bps=None;status="TRANSFER_COST_UNVERIFIED"

                    rows.append({
                      "utc":utc(),"base":base,"capital_usdt":cap,
                      "buy_venue":buy,"sell_venue":sell,
                      "buy_vwap":buy_px,"sell_vwap":sell_px,
                      "trade_gross_bps":gross/cap*10000,
                      "trade_fee_bps":trade_fee/cap*10000,
                      "asset_network":asset_route["network"] if asset_route else None,
                      "asset_transfer_usd":asset_transfer_usd,
                      "usdt_network":usdt_route["network"] if usdt_route else None,
                      "usdt_transfer_usd":usdt_transfer_usd,
                      "net_lock_usd":net,"net_lock_bps":net_bps,"status":status
                    })
    rows.sort(key=lambda x:(x["net_lock_bps"] if x["net_lock_bps"] is not None else -1e99),reverse=True)
    return rows,coverage

def append(rows):
    good=[r for r in rows if r["status"]=="EXECUTABLE_CANDIDATE"]
    if not good:return
    new=not LEDGER.exists()
    with LEDGER.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS)
        if new:w.writeheader()
        for r in good:w.writerow({k:r.get(k,"") for k in FIELDS})

def main():
    rows,coverage=scan()
    append(rows)
    report={
      "utc":utc(),"coverage":coverage,"tested":len(rows),
      "transfer_verified":sum(r["status"]!="TRANSFER_COST_UNVERIFIED" for r in rows),
      "positive_candidates":sum(r["status"]=="EXECUTABLE_CANDIDATE" for r in rows),
      "top_verified":[r for r in rows if r["net_lock_bps"] is not None][:50],
      "top_unverified":[r for r in rows if r["net_lock_bps"] is None][:30]
    }
    LAST.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
      "coverage":coverage,"tested":report["tested"],
      "transfer_verified":report["transfer_verified"],
      "positive_candidates":report["positive_candidates"],
      "top_verified":report["top_verified"][:15]
    },ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
