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

def hypothetical_exit(anchor,ib,lb,history,asof_ms=None):
    """A research-only exit mark: every funding event must precede BOTH exit books.

    An anchor has no fills, so neither historical cash flows nor the exit mark
    can be booked to the RUB virtual account.
    """
    if anchor["source_entry_was_filled"]:
        raise ValueError("anchored source must have zero fills")
    i_ms=int(ib["exchange_ms"])
    l_ms=int(lb["exchange_ms"])
    entry_i_ms,entry_l_ms=(int(v) for v in anchor["first_book_exchange_utc_ms"])
    if i_ms<=entry_i_ms or l_ms<=entry_l_ms:
        return {"state":"NOT_NEWER_THAN_ENTRY_QUOTE"}
    if abs(i_ms-l_ms)>1500:
        return {"state":"DESYNCHRONIZED_MARK"}
    if asof_ms is not None:
        now=int(asof_ms)
        if any(not -1000<=now-ts<=15000 for ts in (i_ms,l_ms)):
            return {"state":"STALE_OR_FUTURE_EXIT_BOOK"}
    if ib["bids"][0][0]>=ib["asks"][0][0] or lb["bids"][0][0]>=lb["asks"][0][0]:
        return {"state":"CROSSED_EXIT_BOOK"}
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
    exit_quote_cutoff_ms=min(i_ms,l_ms)
    anchor_first_observed_ms=int(anchor.get("first_observed_utc_ms",max(entry_i_ms,entry_l_ms)))
    future_rates=[]
    excluded_late=0
    excluded_before_anchor=0
    for t,ir,lr in history["pairs"]:
        funding_ms=int(t)
        if funding_ms>exit_quote_cutoff_ms:
            excluded_late+=1
        elif funding_ms<=anchor_first_observed_ms:
            excluded_before_anchor+=1
        else:
            future_rates.append((funding_ms,face*D(ir)-nominal*D(lr)))
    cumulative=sum((v for _,v in future_rates),Decimal(0))
    return {"state":"RESEARCH_MARK_UNFILLED",
        "new_inverse_exit_ask_usd":str(ie),
        "new_linear_exit_bid_usdt":str(le),
        "price_pnl_usdt_proxy":str(inv_usdt_proxy+lin_pnl),
        "full_roundtrip_fee_usdt_proxy":str(fees),
        "post_entry_settled_funding_count":len(future_rates),
        "exit_quote_cutoff_utc_ms":exit_quote_cutoff_ms,
        "funding_points_excluded_after_exit":excluded_late,
        "funding_points_excluded_pre_anchor":excluded_before_anchor,
        "asof_utc_ms":int(asof_ms) if asof_ms is not None else None,
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
    result=hypothetical_exit(a,ib,lb,paid,asof_ms=now)
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
