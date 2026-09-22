from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"
OUT.mkdir(parents=True, exist_ok=True)

CAPITALS = [100.0, 1000.0, 10000.0]
BASES = ["BTC", "ETH"]
QUOTES = ["USDT", "USDC", "USD"]

SAME_EXCHANGE_IDS = ["bybit", "okx", "gateio", "bitget", "binance"]

def now():
    return datetime.now(timezone.utc).isoformat()

def vwap(levels, quote_amount):
    remain = quote_amount
    base = 0.0
    quote_done = 0.0
    for px, qty in levels:
        px = float(px); qty = float(qty)
        level_quote = px * qty
        take = min(remain, level_quote)
        quote_done += take
        base += take / px
        remain -= take
        if remain <= 1e-9:
            break
    if remain > 1e-6 or base <= 0:
        return None
    return quote_done / base

def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = (len(xs)-1)*p
    lo = int(math.floor(i)); hi = int(math.ceil(i))
    if lo == hi:
        return xs[lo]
    f = i-lo
    return xs[lo]*(1-f)+xs[hi]*f

def fee_for(ex, market, fallback):
    v = market.get("taker")
    if isinstance(v, (int, float)) and v >= 0:
        return float(v)
    try:
        x = ex.fees["trading"]["taker"]
        if isinstance(x, (int, float)) and x >= 0:
            return float(x)
    except Exception:
        pass
    return fallback

def discover_same(exchange_id):
    cls = getattr(ccxt, exchange_id)
    ex = cls({"enableRateLimit": True, "timeout": 15000})
    ex.load_markets()
    pairs = {}
    for base in BASES:
        chosen = None
        for quote in QUOTES:
            spots = [m for m in ex.markets.values()
                     if m.get("spot") and m.get("active", True)
                     and m.get("base") == base and m.get("quote") == quote]
            swaps = [m for m in ex.markets.values()
                     if m.get("swap") and m.get("active", True)
                     and m.get("base") == base and m.get("quote") == quote]
            if spots and swaps:
                spot = sorted(spots, key=lambda m: (m.get("symbol") or ""))[0]
                linear = [m for m in swaps if m.get("linear")]
                swap = (linear or swaps)[0]
                chosen = {
                    "base": base, "quote": quote,
                    "spot_symbol": spot["symbol"],
                    "swap_symbol": swap["symbol"],
                    "spot_market": spot,
                    "swap_market": swap,
                }
                break
        if chosen:
            pairs[base] = chosen
    if len(pairs) < 2:
        raise RuntimeError(f"missing BTC/ETH spot+swap pairs; found {list(pairs)}")
    # Probe one real pair now.
    p = pairs["BTC"]
    ex.fetch_order_book(p["spot_symbol"], 20)
    ex.fetch_order_book(p["swap_symbol"], 20)
    return {"name": exchange_id.upper(), "spot": ex, "deriv": ex, "pairs": pairs}

def discover_kraken():
    spot = ccxt.kraken({"enableRateLimit": True, "timeout": 15000})
    deriv = ccxt.krakenfutures({"enableRateLimit": True, "timeout": 15000})
    spot.load_markets(); deriv.load_markets()
    pairs = {}
    for base in BASES:
        spots = [m for m in spot.markets.values()
                 if m.get("spot") and m.get("active", True)
                 and m.get("base") == base and m.get("quote") == "USD"]
        swaps = [m for m in deriv.markets.values()
                 if m.get("swap") and m.get("active", True)
                 and m.get("base") == base and m.get("quote") == "USD"]
        if not spots or not swaps:
            raise RuntimeError(f"Kraken missing {base}/USD spot or perpetual")
        pairs[base] = {
            "base": base, "quote": "USD",
            "spot_symbol": spots[0]["symbol"],
            "swap_symbol": swaps[0]["symbol"],
            "spot_market": spots[0],
            "swap_market": swaps[0],
        }
    spot.fetch_order_book(pairs["BTC"]["spot_symbol"], 20)
    deriv.fetch_order_book(pairs["BTC"]["swap_symbol"], 20)
    return {"name": "KRAKEN", "spot": spot, "deriv": deriv, "pairs": pairs}

def get_funding(ex, symbol):
    try:
        if ex.has.get("fetchFundingRate"):
            r = ex.fetch_funding_rate(symbol)
            rate = r.get("fundingRate")
            nxt = r.get("fundingTimestamp")
            return float(rate or 0.0), nxt
    except Exception:
        pass
    return 0.0, None

def probe():
    coverage = []
    working = []
    for exid in SAME_EXCHANGE_IDS:
        t0 = time.time()
        try:
            item = discover_same(exid)
            working.append(item)
            coverage.append({"provider": item["name"], "ok": True, "seconds": round(time.time()-t0, 3)})
            print("WORKING", item["name"], flush=True)
        except Exception as e:
            coverage.append({"provider": exid.upper(), "ok": False, "seconds": round(time.time()-t0, 3),
                             "error": f"{type(e).__name__}: {e}"[:500]})
            print("BLOCKED", exid.upper(), type(e).__name__, str(e)[:180], flush=True)

    t0 = time.time()
    try:
        item = discover_kraken()
        working.append(item)
        coverage.append({"provider": "KRAKEN", "ok": True, "seconds": round(time.time()-t0, 3)})
        print("WORKING KRAKEN", flush=True)
    except Exception as e:
        coverage.append({"provider": "KRAKEN", "ok": False, "seconds": round(time.time()-t0, 3),
                         "error": f"{type(e).__name__}: {e}"[:500]})
        print("BLOCKED KRAKEN", type(e).__name__, str(e)[:180], flush=True)

    (OUT/"coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    return working

FIELDS = [
    "utc","provider","symbol","quote","capital",
    "spot_vwap_buy","perp_vwap_sell","basis_bps",
    "spot_taker_fee","perp_taker_fee","roundtrip_fee_bps",
    "funding_rate","net_one_funding_bps","snapshot_gap_ms"
]

def collect(minutes):
    providers = probe()
    if not providers:
        raise SystemExit("No reachable provider from GitHub runner")

    # Prefer up to 3 sources to keep the request rate controlled.
    providers = providers[:3]
    out = OUT/"observations.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        end = time.time() + minutes*60
        cycle = 0
        while time.time() < end:
            cycle += 1
            for item in providers:
                name = item["name"]
                sx = item["spot"]; dx = item["deriv"]
                for base in BASES:
                    p = item["pairs"][base]
                    try:
                        t1 = int(time.time()*1000)
                        sb = sx.fetch_order_book(p["spot_symbol"], 50)
                        mid = int(time.time()*1000)
                        db = dx.fetch_order_book(p["swap_symbol"], 50)
                        t2 = int(time.time()*1000)
                        fr, _ = get_funding(dx, p["swap_symbol"])
                        sf = fee_for(sx, p["spot_market"], 0.001)
                        df = fee_for(dx, p["swap_market"], 0.0006)
                        rt = 2*(sf+df)*10000
                        for cap in CAPITALS:
                            sp = vwap(sb["asks"], cap)
                            pp = vwap(db["bids"], cap)
                            if sp is None or pp is None:
                                continue
                            basis = (pp/sp-1)*10000
                            net = basis + fr*10000 - rt
                            w.writerow({
                                "utc": now(), "provider": name, "symbol": base,
                                "quote": p["quote"], "capital": cap,
                                "spot_vwap_buy": f"{sp:.10f}",
                                "perp_vwap_sell": f"{pp:.10f}",
                                "basis_bps": f"{basis:.6f}",
                                "spot_taker_fee": sf, "perp_taker_fee": df,
                                "roundtrip_fee_bps": f"{rt:.6f}",
                                "funding_rate": f"{fr:.12f}",
                                "net_one_funding_bps": f"{net:.6f}",
                                "snapshot_gap_ms": max(0, t2-mid),
                            })
                        f.flush()
                        print(now(), name, base, "cycle", cycle, flush=True)
                    except Exception as e:
                        print("SAMPLE_ERROR", name, base, type(e).__name__, str(e)[:220], flush=True)
            time.sleep(3)

def summarize():
    rows = []
    p = OUT/"observations.csv"
    if not p.exists():
        return
    with p.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    groups = {}
    for r in rows:
        try:
            k=(r["provider"],r["symbol"],r["quote"],r["capital"])
            groups.setdefault(k,[]).append(float(r["net_one_funding_bps"]))
        except Exception:
            pass
    o=OUT/"summary.csv"
    fields=["provider","symbol","quote","capital","n","positive_pct","p05_bps","median_bps","mean_bps","p95_bps","p99_bps","max_bps"]
    with o.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for (prov,sym,q,cap),xs in sorted(groups.items()):
            n=len(xs)
            w.writerow({
                "provider":prov,"symbol":sym,"quote":q,"capital":cap,"n":n,
                "positive_pct":round(100*sum(x>0 for x in xs)/n,4),
                "p05_bps":round(pct(xs,.05),6),
                "median_bps":round(statistics.median(xs),6),
                "mean_bps":round(statistics.fmean(xs),6),
                "p95_bps":round(pct(xs,.95),6),
                "p99_bps":round(pct(xs,.99),6),
                "max_bps":round(max(xs),6),
            })

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--minutes",type=float,default=3.0)
    a=ap.parse_args()
    collect(a.minutes)
    summarize()
