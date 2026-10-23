#!/usr/bin/env python3
"""NORM 03B M1 — MOEX native calendar spread vs. synthetic 2-leg reversal.

Strict read-only PUBLIC research. NO exchange orders and NO shadow fills:
- native spread HTML page has no exchange timestamp;
- MOEX public ISS futures quotes are roughly 15-minute delayed;
- public native spread symbols can have stale is_traded/listed_till fields;
- matching engine may remove crossed native/synthetic prices immediately.
Reports indicative price-crosses and conservative exchange-only fee estimates,
never executable arbitrage PnL or changes to the RUB 50,000 portfolio.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import html
import itertools
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

ISS = (
 "https://iss.moex.com/iss/engines/futures/markets/forts/boards/rfud/securities.json?"
 "iss.meta=off&iss.only=securities,marketdata&"
 "securities.columns=SECID,SHORTNAME,ASSETCODE,LASTTRADEDATE,INITIALMARGIN,MINSTEP,STEPPRICE,BUYSELLFEE&"
 "marketdata.columns=SECID,BID,OFFER,UPDATETIME,SYSTIME&limit=1000"
)
PAGE = "https://www.moex.com/ru/derivatives/spreads/calendar-spreads.aspx"
DOC = "https://www.moex.com/files/4h3j47jf4g2dnswvk6fb4ggfcc"
QUOTE_RE = re.compile(
 r'<td nowrap="nowrap">([^<]+)</td><td>([^<]+)</td><td>([^<]+)</td><td>([^<]+)</td>'
)
MAX_INPUT = 3_000_000
CAPITAL_RUB = Decimal("50000")
RESERVE_RUB = Decimal("10000")
MAX_LINKED_RUB = CAPITAL_RUB - RESERVE_RUB


def d(value):
    if value is None or isinstance(value, bool):
        raise ValueError("null or bool")
    out = Decimal(str(value).strip().replace(",", "."))
    if not out.is_finite():
        raise ValueError("nonfinite")
    return out


def fetch(url):
    if url not in {ISS, PAGE}:
        raise ValueError("unexpected source")
    req = Request(url, headers={"User-Agent": "NORM03B-MOEX-M1/1", "Accept": "application/json,text/html"}, method="GET")
    with urlopen(req, timeout=15) as res:
        if res.status != 200:
            raise ValueError("HTTP_NOT_OK")
        payload = res.read(MAX_INPUT + 1)
    if len(payload) > MAX_INPUT:
        raise ValueError("source too large")
    return payload.decode("utf-8"), hashlib.sha256(payload).hexdigest()


def table_rows(block):
    columns = block["columns"]
    if len(columns) != len(set(columns)):
        raise ValueError("duplicate provider columns")
    for row in block["data"]:
        if len(row) != len(columns):
            raise ValueError("misaligned provider row")
        yield dict(zip(columns, row))


def native_quotes(page):
    out = {}
    for name, _last, bid, ask in QUOTE_RE.findall(page):
        name = html.unescape(name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,65}", name):
            continue
        try:
            x, y = d(bid), d(ask)
            if x > y:
                continue
        except (InvalidOperation, ValueError):
            continue
        out[name] = {"bid": x, "ask": y}
    return out


def spread_name(a, b):
    base = a.get("ASSETCODE")
    if not isinstance(base, str) or a.get("ASSETCODE") != b.get("ASSETCODE"):
        return None
    na, nb = a.get("SHORTNAME"), b.get("SHORTNAME")
    if not isinstance(na, str) or not isinstance(nb, str):
        return None
    if not na.startswith(base + "-") or not nb.startswith(base + "-"):
        return None
    return na + "-" + nb[len(base) + 1 :]


def synthetic_prices(near_bid, near_ask, far_bid, far_ask):
    # Quoting convention in official MOEX Apr-2026 synthetic matching spec:
    # calendar spread = far contract - near contract.
    nb, na, fb, fa = map(d, (near_bid, near_ask, far_bid, far_ask))
    if min(nb, na, fb, fa) <= 0 or nb >= na or fb >= fa:
        return None
    return {"synthetic_bid": fb - na, "synthetic_ask": fa - nb}


def evaluate(spread, a, b, aq, bq):
    synth = synthetic_prices(aq.get("BID"), aq.get("OFFER"), bq.get("BID"), bq.get("OFFER"))
    if synth is None:
        return None
    try:
        mn, mf = d(a["INITIALMARGIN"]), d(b["INITIALMARGIN"])
        tick_n, tick_f = d(a["MINSTEP"]), d(b["MINSTEP"])
        step_n, step_f = d(a["STEPPRICE"]), d(b["STEPPRICE"])
        fn, ff = d(a["BUYSELLFEE"]), d(b["BUYSELLFEE"])
        if min(mn, mf, tick_n, tick_f, step_n, step_f) <= 0 or min(fn, ff) < 0:
            return None
        # Different multipliers/tick money produce invalid additive quote PnL.
        if abs(step_n / tick_n - step_f / tick_f) > Decimal("0.001"):
            return None
        native_bid, native_ask = spread["bid"], spread["ask"]
        # sell native, buy far/sell near outrights:
        sell_native = native_bid - synth["synthetic_ask"]
        # buy native, sell far/buy near outrights:
        buy_native = synth["synthetic_bid"] - native_ask
        best = max(sell_native, buy_native)
        rate = step_n / tick_n
        gross = best * rate
        # 4 underlying fills in two opposing positions, estimated full exchange fee.
        fee_exchange_four_fills = 2 * (fn + ff)
        tentative_after_exchange = gross - fee_exchange_four_fills
        margin_two = mn + mf
        margin_four_conservative = 2 * margin_two
        direction = "SELL_NATIVE_BUY_SYNTH" if sell_native >= buy_native else "BUY_NATIVE_SELL_SYNTH"
        return {
            "spread": spread_name(a, b),
            "near_contract": a["SECID"],
            "far_contract": b["SECID"],
            "native_bid": str(native_bid),
            "native_ask": str(native_ask),
            "synthetic_bid": str(synth["synthetic_bid"]),
            "synthetic_ask": str(synth["synthetic_ask"]),
            "best_direction": direction,
            "cross_points": str(best),
            "gross_rub_per_equal_contract_set": str(gross),
            "exchange_fee_four_fills_est_rub": str(fee_exchange_four_fills),
            "indicative_after_exchange_fee_rub": str(tentative_after_exchange),
            "margin_two_outrights_rub": str(margin_two),
            "margin_four_fills_stress_rub": str(margin_four_conservative),
            "within_40k_stress_cap": margin_four_conservative <= MAX_LINKED_RUB,
            "near_exchange_updatetime": aq.get("UPDATETIME"),
            "far_exchange_updatetime": bq.get("UPDATETIME"),
            "near_iss_publication": aq.get("SYSTIME"),
            "far_iss_publication": bq.get("SYSTIME"),
            "native_html_exchange_timestamp": None,
            "native_iss_board_status": "NOT_VERIFIED",
            "broker_tariff_verified": False,
            "book_depth_verified": False,
            "causal_simultaneity_proven": False,
            "portfolio_executable": False,
            "paper_fill": False,
            "real_order": False,
            "eligibility": "INDICATIVE_CROSS_ONLY"
        }
    except (TypeError, InvalidOperation, ValueError, KeyError, ZeroDivisionError):
        return None


def analyse(future, page):
    securities = list(table_rows(future["securities"]))
    marketdata = {x["SECID"]:x for x in table_rows(future["marketdata"])}
    native = native_quotes(page)
    groups = defaultdict(list)
    for x in securities:
        groups[x.get("ASSETCODE", "")].append(x)
    checks = []
    for rows in groups.values():
        rows.sort(key=lambda x:x.get("LASTTRADEDATE",""))
        for a, b in itertools.combinations(rows, 2):
            name = spread_name(a, b)
            if name not in native or a["SECID"] not in marketdata or b["SECID"] not in marketdata:
                continue
            check = evaluate(native[name], a, b, marketdata[a["SECID"]], marketdata[b["SECID"]])
            if check:
                checks.append(check)
    candidates = [r for r in checks if d(r["cross_points"])>0]
    within = [r for r in candidates if r["within_40k_stress_cap"]]
    positive_exchange = [r for r in within if d(r["indicative_after_exchange_fee_rub"])>0]
    positive_exchange.sort(key=lambda r:d(r["indicative_after_exchange_fee_rub"]),reverse=True)
    checks.sort(key=lambda r:d(r["indicative_after_exchange_fee_rub"]),reverse=True)
    return {
        "schema":"NORM03B_M1_MOEX_NATIVE_VS_SYNTHETIC_READONLY_V1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "market":"MOEX public futures RFUD and official native spread HTML",
        "source_iss":ISS, "source_html":PAGE, "official_price_definition":DOC,
        "number_of_futures":len(securities),
        "number_of_native_spread_html_quotes":len(native),
        "matched_native_vs_synthetic_comparisons":len(checks),
        "gross_apparent_crosses":len(candidates),
        "gross_apparent_crosses_within_40k_stress":len(within),
        "exchange_fee_only_positive_indications":len(positive_exchange),
        "best_indications":positive_exchange[:15],
        "best_all":checks[:5],
        "limits":["MOEX ISS public leg bid/ask delayed about 15 min",
                  "HTML native spread price has NO exchange timestamp",
                  "native symbols may have stale is_traded/listed_till via ISS",
                  "native exchange synthetic matching may consume apparent edge first",
                  "linked collateral and clearing margin offsets NOT independently verified",
                  "broker/clearing/exchange fee completeness NOT independently verified",
                  "trade depth and partial-fill risks NOT verified",
                  "apparent gross crossing NEVER credits RUB account"],
        "status":"QUOTES_ASYNCHRONOUS_NO_EXECUTABLE_CYCLE",
        "virtual_rub_cash":"50000", "virtual_rub_equity":"50000",
        "paper_fills":0, "real_orders":0, "closed_cycles":0
    }


def main():
    arg=argparse.ArgumentParser()
    arg.add_argument("--out",type=Path,required=True)
    ns=arg.parse_args()
    iss, h1=fetch(ISS)
    page,h2=fetch(PAGE)
    report=analyse(json.loads(iss),page)
    report["source_sha256"]={"public_iss":h1,"native_web":h2}
    ns.out.parent.mkdir(parents=True,exist_ok=True)
    ns.out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({x:report[x] for x in [
        "number_of_futures","number_of_native_spread_html_quotes",
        "matched_native_vs_synthetic_comparisons",
        "gross_apparent_crosses","gross_apparent_crosses_within_40k_stress",
        "exchange_fee_only_positive_indications",
        "status","paper_fills","real_orders"]},sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
