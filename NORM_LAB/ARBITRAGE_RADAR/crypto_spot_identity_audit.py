from __future__ import annotations

import json, time
from pathlib import Path
import ccxt

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"results"
OUT.mkdir(parents=True,exist_ok=True)

VENUES=["okx","bitget","gate","mexc","htx"]
ASSETS=["OP","HBAR","APT","ICP","DOT","ARB","WIF","SUI"]

def safe_currency(ex,code):
    try:
        cs=ex.fetch_currencies()
        c=cs.get(code)
        if not c:return {"available":False}
        nets=[]
        for name,n in (c.get("networks") or {}).items():
            nets.append({
              "network":name,
              "deposit":n.get("deposit"),
              "withdraw":n.get("withdraw"),
              "active":n.get("active"),
              "fee":n.get("fee")
            })
        return {
          "available":True,
          "active":c.get("active"),
          "deposit":c.get("deposit"),
          "withdraw":c.get("withdraw"),
          "networks":nets
        }
    except Exception as e:
        return {"available":False,"error":f"{type(e).__name__}: {e}"[:500]}

def main():
    report={"venues":{},"pairwise":{}}
    snapshots={}

    for exid in VENUES:
        try:
            ex=getattr(ccxt,exid)({"enableRateLimit":True,"timeout":20000})
            ex.load_markets()
            v={}
            for base in ASSETS:
                ms=[m for m in ex.markets.values()
                    if m.get("spot") and m.get("active",True)
                    and m.get("base")==base and m.get("quote")=="USDT"]
                if not ms:continue
                m=ms[0]
                try:
                    ob=ex.fetch_order_book(m["symbol"],20)
                    bid=float(ob["bids"][0][0]) if ob.get("bids") else None
                    ask=float(ob["asks"][0][0]) if ob.get("asks") else None
                except Exception as e:
                    bid=ask=None
                    ob_error=f"{type(e).__name__}: {e}"[:500]
                else:
                    ob_error=None
                cur=safe_currency(ex,base)
                row={
                  "symbol":m.get("symbol"),"market_id":m.get("id"),
                  "base":m.get("base"),"quote":m.get("quote"),"active":m.get("active"),
                  "type":m.get("type"),"spot":m.get("spot"),
                  "precision":m.get("precision"),"limits":m.get("limits"),
                  "bid":bid,"ask":ask,
                  "mid":((bid+ask)/2 if bid and ask else None),
                  "orderbook_error":ob_error,
                  "currency":cur
                }
                v[base]=row
                if row["mid"]:
                    snapshots[(exid.upper(),base)]=row
                if exid=="mexc":time.sleep(.3)
            report["venues"][exid.upper()]=v
        except Exception as e:
            report["venues"][exid.upper()]={"error":f"{type(e).__name__}: {e}"[:700]}

    for base in ASSETS:
        rows=[(v,d) for (v,b),d in snapshots.items() if b==base]
        pairs=[]
        for i in range(len(rows)):
            for j in range(i+1,len(rows)):
                a,A=rows[i];b,B=rows[j]
                basis=(B["mid"]/A["mid"]-1)*10000
                executable_ab=(B["bid"]/A["ask"]-1)*10000 if A["ask"] and B["bid"] else None
                executable_ba=(A["bid"]/B["ask"]-1)*10000 if B["ask"] and A["bid"] else None
                pairs.append({
                  "venue_a":a,"venue_b":b,
                  "mid_basis_bps":basis,
                  "buy_a_sell_b_bps":executable_ab,
                  "buy_b_sell_a_bps":executable_ba
                })
        pairs.sort(key=lambda x:max(abs(x["mid_basis_bps"]),abs(x["buy_a_sell_b_bps"] or 0),abs(x["buy_b_sell_a_bps"] or 0)),reverse=True)
        report["pairwise"][base]=pairs

    (OUT/"crypto_spot_identity_audit.json").write_text(
      json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    brief={}
    for base,pairs in report["pairwise"].items():
        brief[base]=pairs[:5]
    print(json.dumps(brief,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
