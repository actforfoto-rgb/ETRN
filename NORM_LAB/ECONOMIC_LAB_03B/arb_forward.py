#!/usr/bin/env python3
"""NORM LAB 03B — read-only, exclusively paired cross-venue arbitrage.

Public Gate/Bitget/OKX spot books only. No keys, orders or portfolio mutation.
A paper witness is never a real fill. Fees/rebalancing are stress assumptions.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

SYMBOLS = ("DOGE", "AVAX", "LINK", "LTC", "NEAR", "SUI", "TRX", "BCH", "APT", "ETC")
VENUES = ("Gate", "Bitget", "OKX")
FEE = {"Gate": Decimal("0.001"), "Bitget": Decimal("0.001"), "OKX": Decimal("0.002")}
AMOUNTS = (Decimal("100"), Decimal("250"))
REBALANCE_RATE = Decimal("0.002")  # assumed BOTH transfer/replenishment costs
REBALANCE_FIXED = Decimal("0.5")  # USDT stress; cannot prove executable transfer
MAX_SKEW_MS = 1500
MAX_AGE_MS = 5000
MAX_FUTURE_MS = 1000

def dec(obj):
    x = Decimal(str(obj))
    if not x.is_finite():
        raise ValueError("nonfinite")
    return x

def now_ms():
    return time.time_ns() // 1_000_000

@dataclass(frozen=True)
class Book:
    symbol: str
    venue: str
    bids: tuple
    asks: tuple
    exchange_ms: int
    received_ms: int
    sha256: str

    def valid(self):
        age = self.received_ms - self.exchange_ms
        if not (-MAX_FUTURE_MS <= age <= MAX_AGE_MS):
            return False
        if not self.asks or not self.bids:
            return False
        for side, ascending in ((self.asks, True), (self.bids, False)):
            if any(p <= 0 or q <= 0 for p, q in side):
                return False
            prices = [p for p, _ in side]
            if prices != sorted(prices, reverse=not ascending) or len(set(prices)) != len(prices):
                return False
        return self.bids[0][0] < self.asks[0][0]

def url(symbol, venue):
    if symbol not in SYMBOLS or venue not in VENUES:
        raise ValueError("unapproved market")
    if venue == "Gate":
        return "https://api.gateio.ws/api/v4/spot/order_book?currency_pair=" + symbol + "_USDT&limit=10&with_id=true"
    if venue == "Bitget":
        return "https://api.bitget.com/api/v3/market/orderbook?category=SPOT&symbol=" + symbol + "USDT&limit=10"
    return "https://www.okx.com/api/v5/market/books?instId=" + symbol + "-USDT&sz=10"

def parse(symbol, venue, payload, received_ms, sha256):
    if venue == "Gate":
        raw = payload
        ask, bid, ts = raw["asks"], raw["bids"], raw["update"]
    elif venue == "Bitget":
        if payload.get("code") != "00000":
            raise ValueError("Bitget provider error")
        raw = payload["data"]
        ask, bid, ts = raw["a"], raw["b"], raw["ts"]
    elif venue == "OKX":
        if payload.get("code") != "0":
            raise ValueError("OKX provider error")
        raw = payload["data"][0]
        ask, bid, ts = raw["asks"], raw["bids"], raw["ts"]
    else:
        raise ValueError("unapproved venue")
    def levels(seq):
        return tuple((dec(item[0]), dec(item[1])) for item in seq[:10])
    return Book(symbol, venue, levels(bid), levels(ask), int(ts), received_ms, sha256)

def fetch(symbol, venue):
    req = Request(url(symbol, venue), headers={"Accept":"application/json", "User-Agent":"NORM03B/2.0"}, method="GET")
    with urlopen(req, timeout=6.5) as res:
        if res.status != 200:
            raise ValueError("HTTP_NOT_OK")
        body = res.read(250001)
        received = now_ms()
    if len(body) > 250000:
        raise ValueError("TOO_LARGE")
    record = parse(symbol, venue, json.loads(body), received, hashlib.sha256(body).hexdigest())
    if not record.valid():
        raise ValueError("STALE_OR_BAD_BOOK")
    return record

def walk(levels, qty):
    if qty <= 0:
        return None
    left = qty
    total = Decimal(0)
    for price, size in levels:
        used = min(left, size)
        total += used * price
        left -= used
        if left <= 0:
            return total
    return None

def evaluate(buy, sell, notional):
    # A paired mechanism: simultaneously buy the same base on A and sell on B.
    # Prefunding requires USDT on A AND base inventory on B.
    if buy.symbol != sell.symbol or buy.venue == sell.venue:
        return None
    if not buy.valid() or not sell.valid():
        return None
    skew = abs(buy.exchange_ms - sell.exchange_ms)
    if skew > MAX_SKEW_MS:
        return None
    qty = notional / buy.asks[0][0]
    cost, revenue = walk(buy.asks, qty), walk(sell.bids, qty)
    if cost is None or revenue is None:
        return None
    gross = revenue - cost
    commissions = cost * FEE[buy.venue] + revenue * FEE[sell.venue]
    replenishment = cost * REBALANCE_RATE + REBALANCE_FIXED
    net = gross - commissions - replenishment
    linked = cost + revenue  # capital on BOTH pre-funded venues; optimistic
    return {
        "asset":buy.symbol, "buy":buy.venue, "sell":sell.venue,
        "amount_usdt":str(notional), "qty_base":str(qty),
        "gross_usdt":str(gross), "trade_fees_usdt":str(commissions),
        "rebalance_scenario_usdt":str(replenishment),
        "net_scenario_usdt":str(net), "linked_usdt_equivalent":str(linked),
        "net_bp_linked":str(net / linked * 10000),
        "buy_exchange_ms":buy.exchange_ms, "sell_exchange_ms":sell.exchange_ms,
        "buy_received_ms":buy.received_ms, "sell_received_ms":sell.received_ms,
        "skew_ms":skew, "buy_sha256":buy.sha256, "sell_sha256":sell.sha256,
        "model":"UNEXECUTED_PAIRED_BOOK_SCREEN"}

def snapshot(workers=9):
    books = {}
    errors = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        jobs = {ex.submit(fetch, asset, venue):(asset, venue)
                for asset in SYMBOLS for venue in VENUES}
        for result in as_completed(jobs):
            name = jobs[result]
            try:
                books[name] = result.result()
            except (OSError, ValueError, KeyError, TypeError, IndexError, InvalidOperation, TimeoutError) as err:
                k = type(err).__name__
                errors[k] = errors.get(k, 0) + 1
            except Exception as err:
                errors["UNEXPECTED_" + type(err).__name__] = errors.get("UNEXPECTED_" + type(err).__name__, 0) + 1
    routes = []
    for asset in SYMBOLS:
        for b in VENUES:
            for s in VENUES:
                if b == s or (asset,b) not in books or (asset,s) not in books:
                    continue
                for nominal in AMOUNTS:
                    r = evaluate(books[(asset,b)], books[(asset,s)], nominal)
                    if r is not None:
                        routes.append(r)
    routes.sort(key=lambda q:dec(q["net_scenario_usdt"]),reverse=True)
    return {
        "time_utc_ms":now_ms(), "valid_books":len(books), "errors":errors,
        "paired_routes":len(routes), "gross_positive":sum(dec(r["gross_usdt"])>0 for r in routes),
        "positive_after_taker_before_rebalancing":sum(dec(r["gross_usdt"])-dec(r["trade_fees_usdt"])>0 for r in routes),
        "positive_after_costs":sum(dec(r["net_scenario_usdt"])>0 for r in routes),
        "best_gross_route":max(routes,key=lambda r:dec(r["gross_usdt"])) if routes else None,
        "best_routes":routes[:4]
    }, routes

def key(r):
    return (r["asset"], r["buy"], r["sell"], r["amount_usdt"])

def capture(seconds, interval):
    start = time.monotonic()
    pending = {}
    witness = []
    missed = 0
    summaries = []
    unique_positive = set()
    while time.monotonic() - start < seconds and len(summaries) < 16:
        overview, routes = snapshot()
        best = overview["best_routes"][0] if overview["best_routes"] else None
        summaries.append({**{k:v for k,v in overview.items() if k!="best_routes"},
                          "best_net_route":best,
                          "best_net_gap_bp_linked":str(max(dec(0),-dec(best["net_scenario_usdt"]))/dec(best["linked_usdt_equivalent"])*10000) if best else None})
        bykey = {key(r):r for r in routes}
        for k, previous in list(pending.items()):
            latest = bykey.get(k)
            if latest and (latest["buy_exchange_ms"] > previous["buy_exchange_ms"]
                    and latest["sell_exchange_ms"] > previous["sell_exchange_ms"]):
                if dec(latest["net_scenario_usdt"]) > 0:
                    witness.append({"entry_quote":previous,"next_quote":latest,
                                    "status":"PROSPECTIVE_PRICE_WITNESS_NOT_A_FILL",
                                    "owner_executable":False})
                else:
                    missed += 1
                del pending[k]
        for r in routes:
            if dec(r["net_scenario_usdt"])>0 and key(r) not in pending:
                tag=(key(r),r["buy_exchange_ms"],r["sell_exchange_ms"])
                if tag not in unique_positive:
                    pending[key(r)]=r
                    unique_positive.add(tag)
        if time.monotonic()-start >= seconds:
            break
        time.sleep(min(interval, max(0,seconds-(time.monotonic()-start))))
    return {
        "schema":"NORM03B_HEDGED_M4_FWD_V1", "created_utc_ms":now_ms(),
        "mode":"PUBLIC_READONLY_ZERO_ORDERS",
        "quotes":summaries, "episodes_with_positive_next_quote":witness[:10],
        "positive_first_quote_unique":len(unique_positive),
        "forward_negative_or_lost_opportunities":missed,
        "pending_positive_at_end":len(pending),
        "fees_are_assumptions":{k:str(v) for k,v in FEE.items()},
        "replenishment_is_assumption":True,
        "onchain_or_transfer_completion_evidence":False,
        "account_access_and_leg_fills_verified":False,
        "full_closed_arbitrage_cycles":0, "real_orders":0, "broker_paper_orders":0,
        "virtual_rub_balance_unmodified":"50000",
        "reason":"Price witness != fill; owner RUB/USDT conversion, permissions and actual two-way inventory restoration not proved."
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--seconds",type=int,default=90)
    ap.add_argument("--interval",type=float,default=8)
    ap.add_argument("--out",type=Path,required=True)
    ns=ap.parse_args()
    if not 10<=ns.seconds<=145 or not 3<=ns.interval<=30:
        ap.error("bounded 10..145 seconds, 3..30 interval")
    report=capture(ns.seconds,ns.interval)
    ns.out.parent.mkdir(parents=True,exist_ok=True)
    ns.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"mode":report["mode"],"snapshots":len(report["quotes"]),
        "positive_first_quote_unique":report["positive_first_quote_unique"],
        "positive_next_quote_witnesses":len(report["episodes_with_positive_next_quote"]),
        "closed_cycles":0, "real_orders":0},sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
