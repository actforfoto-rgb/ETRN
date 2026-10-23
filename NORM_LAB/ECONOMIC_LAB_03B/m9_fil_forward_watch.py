"""NORM 03B forward observation: FIL two-perpetual funding hypothesis.

Public data only; archived entry quote was never a trade. Future paid rates
are considered only if their official timestamp is after the fixed anchor.
"""
from __future__ import annotations
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json, argparse
from inverse_linear_funding_gate import D, get, url, parse_book, sweep, clock_ms

ANCHOR=Path(__file__).with_name("m9_fil_forward_anchor.json")
FEE=Decimal("0.0005")

def paid_after_start(inv,lin,start,now):
    def normalize(rows):
        out={}
        for x in rows:
            try:
                t=int(x["fundingTime"])
                if start<t<=now:
                    rate=D(x["realizedRate"])
                    if abs(rate)<=Decimal("0.01"):out[t]=rate
            except (ValueError,TypeError,KeyError):continue
        return out
    a,b=normalize(inv),normalize(lin)
    common=sorted(a.keys() & b.keys())
    return {"pairs":[(t,str(a[t]),str(b[t])) for t in common],
        "unmatched":len(a.keys() ^ b.keys()),"post_anchor_only":True}

def hypothetical_exit(anchor,ib,lb,history):
    if not (ib["exchange_ms"]>anchor["first_book_exchange_utc_ms"][1]
            and lb["exchange_ms"]>anchor["first_book_exchange_utc_ms"][0]):
        return {"state":"NOT_NEWER_THAN_ENTRY_QUOTE"}
    if abs(ib["exchange_ms"]-lb["exchange_ms"])>1500:
        return {"state":"DESYNCHRONIZED_MARK"}
    if anchor["source_entry_was_filled"]:
        raise ValueError("anchored source must have zero fills")
    inv_contracts=D(anchor["inverse_contract_count"])
    lin_contracts=D(anchor["linear_contract_count"])
    lin_qty=D(anchor["linear_base_qty"])
    face=D(anchor["inverse_usd_face_total"])
    ip=D(anchor["inverse_short_bid_entry_usd"])
    lp=D(anchor["linear_long_ask_entry_usdt"])
    ie=sweep(ib,"asks",inv_contracts)
    le=sweep(lb,"bids",lin_contracts)
    if ie is None or le is None:return {"state":"EXIT_DEPTH_UNAVAILABLE"}
    inv_coin_pnl=face*(1/ie-1/ip)
    inv_usdt_proxy=inv_coin_pnl*le
    lin_pnl=lin_qty*(le-lp)
    fees=FEE*(face*2+lin_qty*(lp+le))
    nominal=lin_qty*lp
    future_rates=[(t,face*D(ir)-nominal*D(lr)) for t,ir,lr in history["pairs"]]
    cumulative=sum((v for _,v in future_rates),Decimal(0))
    return {"state":"RESEARCH_MARK_UNFILLED",
        "new_inverse_exit_ask_usd":str(ie),
        "new_linear_exit_bid_usdt":str(le),
        "price_pnl_usdt_proxy":str(inv_usdt_proxy+lin_pnl),
        "full_roundtrip_fee_usdt_proxy":str(fees),
        "post_entry_settled_funding_count":len(future_rates),
        "post_entry_funding_usdt_proxy":str(cumulative),
        "net_current_mark_to_exit_usdt_proxy":str(inv_usdt_proxy+lin_pnl-fees+cumulative),
        "actual_trade_filled":False,"usd_usdt_one_to_one_assumption":True,
        "coin_collateral_and_basis_not_fully_hedged":True}

def observe():
    a=json.loads(ANCHOR.read_text(encoding="utf-8"))
    if a["source_entry_was_filled"] or a["cash_account_rub_untouched"]!=50000:
        raise ValueError("anchor integrity")
    start=a["first_observed_utc_ms"]
    calls={"ib":url("books","FIL","USD"),"lb":url("books","FIL","USDT"),
        "ih":url("history","FIL","USD").replace("limit=30","limit=100"),
        "lh":url("history","FIL","USDT").replace("limit=30","limit=100")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={k:pool.submit(get,v) for k,v in calls.items()}
        r={k:f.result() for k,f in futures.items()}
    ib=parse_book(r["ib"][0],r["ib"][1])
    lb=parse_book(r["lb"][0],r["lb"][1])
    now=clock_ms()
    paid=paid_after_start(r["ih"][0],r["lh"][0],start,now)
    result=hypothetical_exit(a,ib,lb,paid)
    return {"schema":"NORM03B_FIL_POST_ANCHOR_WATCH_V1","anchor_run":a["source_run_id"],
        "anchor_ms":start,"now_ms":now,"book_ms":[ib["exchange_ms"],lb["exchange_ms"]],
        "post_anchor_realized":paid,"hypothetical_mark":result,
        "source_sha256":{k:v[2] for k,v in r.items()},
        "limits":["Archived entry was never filled","Future realized settlement rates not the owner's receipts",
          "Coin collateral, USD/USDT exchange, basis and inverse nonlinearity remain risks"],
        "paper_fills":0,"real_orders":0,"cash_rub":50000,"equity_rub":50000}

def main():
    p=argparse.ArgumentParser();p.add_argument("--out",type=Path,required=True)
    arg=p.parse_args()
    try:result=observe()
    except (OSError,ValueError,KeyError,TypeError) as exc:
        result={"schema":"NORM03B_FIL_POST_ANCHOR_WATCH_V1","status":"DATA_BLOCKED",
            "error":type(exc).__name__,"paper_fills":0,"real_orders":0,
            "cash_rub":50000,"equity_rub":50000}
    arg.out.parent.mkdir(parents=True,exist_ok=True)
    arg.out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({"status":result.get("hypothetical_mark",{}).get("state",result.get("status")),
          "post_signal_settlements":len(result.get("post_anchor_realized",{}).get("pairs",[])),
          "net_unfilled_proxy":result.get("hypothetical_mark",{}).get("net_current_mark_to_exit_usdt_proxy"),
          "real_orders":0,"paper_fills":0}))
if __name__=="__main__":main()
