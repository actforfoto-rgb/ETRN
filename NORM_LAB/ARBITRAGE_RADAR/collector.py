from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results_c02"
OUT.mkdir(parents=True, exist_ok=True)

BASES = ["BTC", "ETH"]
CAPITALS = [100.0, 1000.0, 10000.0]
QUOTES = ["USDT", "USD", "USDC"]

# Explicit conservative taker-fee assumptions for the base retail tier.
# These are screening assumptions, not account-specific fees.
FEE_OVERRIDES = {
    "BITGET": {"spot": 0.0010, "perp": 0.0006, "source": "official_base_tier"},
    "OKX":    {"spot": 0.0010, "perp": 0.0005, "source": "official_regular_global"},
    "KRAKEN": {"spot": 0.0080, "perp": 0.0005, "source": "official_tier1"},
}
SAME_EXCHANGE_IDS = ["okx", "bitget", "gate", "bybit", "binance"]

def now():
    return datetime.now(timezone.utc).isoformat()

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

def choose_pairs(ex):
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
            linear = [m for m in swaps if m.get("linear")]
            if spots and (linear or swaps):
                chosen = {
                    "base": base, "quote": quote,
                    "spot": sorted(spots, key=lambda m: m["symbol"])[0],
                    "swap": sorted(linear or swaps, key=lambda m: m["symbol"])[0],
                }
                break
        if chosen:
            pairs[base] = chosen
    if set(pairs) != set(BASES):
        raise RuntimeError(f"missing pairs: {pairs.keys()}")
    return pairs

def discover_same(exchange_id):
    cls = getattr(ccxt, exchange_id)
    ex = cls({"enableRateLimit": True, "timeout": 15000})
    ex.load_markets()
    pairs = choose_pairs(ex)
    ex.fetch_order_book(pairs["BTC"]["spot"]["symbol"], 20)
    ex.fetch_order_book(pairs["BTC"]["swap"]["symbol"], 20)
    return {"name": exchange_id.upper(), "spot": ex, "deriv": ex, "pairs": pairs}

def discover_kraken():
    spot = ccxt.kraken({"enableRateLimit": True, "timeout": 15000})
    deriv = ccxt.krakenfutures({"enableRateLimit": True, "timeout": 15000})
    spot.load_markets(); deriv.load_markets()
    pairs = {}
    for base in BASES:
        s = [m for m in spot.markets.values()
             if m.get("spot") and m.get("active", True)
             and m.get("base") == base and m.get("quote") == "USD"]
        d = [m for m in deriv.markets.values()
             if m.get("swap") and m.get("active", True)
             and m.get("base") == base and m.get("quote") == "USD"]
        if not s or not d:
            raise RuntimeError(f"missing Kraken {base}/USD")
        pairs[base] = {"base": base, "quote": "USD", "spot": s[0], "swap": d[0]}
    spot.fetch_order_book(pairs["BTC"]["spot"]["symbol"], 20)
    deriv.fetch_order_book(pairs["BTC"]["swap"]["symbol"], 20)
    return {"name": "KRAKEN", "spot": spot, "deriv": deriv, "pairs": pairs}

def funding(ex, symbol):
    try:
        if ex.has.get("fetchFundingRate"):
            r = ex.fetch_funding_rate(symbol)
            return float(r.get("fundingRate") or 0.0), r.get("fundingTimestamp")
    except Exception:
        pass
    return 0.0, None

def fees_for(name):
    if name in FEE_OVERRIDES:
        return FEE_OVERRIDES[name]
    return {"spot": 0.0015, "perp": 0.00075, "source": "conservative_fallback"}

def discover_all():
    coverage = []
    providers = []
    for exid in SAME_EXCHANGE_IDS:
        t0 = time.time()
        try:
            x = discover_same(exid)
            providers.append(x)
            coverage.append({"provider": x["name"], "ok": True, "seconds": round(time.time()-t0,3)})
            print("WORKING", x["name"], flush=True)
        except Exception as e:
            coverage.append({"provider": exid.upper(), "ok": False, "seconds": round(time.time()-t0,3),
                             "error": f"{type(e).__name__}: {e}"[:600]})
            print("BLOCKED", exid.upper(), type(e).__name__, str(e)[:180], flush=True)

    t0 = time.time()
    try:
        x = discover_kraken()
        providers.append(x)
        coverage.append({"provider": "KRAKEN", "ok": True, "seconds": round(time.time()-t0,3)})
        print("WORKING KRAKEN", flush=True)
    except Exception as e:
        coverage.append({"provider": "KRAKEN", "ok": False, "seconds": round(time.time()-t0,3),
                         "error": f"{type(e).__name__}: {e}"[:600]})
        print("BLOCKED KRAKEN", type(e).__name__, str(e)[:180], flush=True)

    (OUT/"coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    return providers

SAME_FIELDS = [
    "utc","provider","symbol","quote","capital",
    "spot_buy_vwap","perp_sell_vwap","raw_basis_bps",
    "spot_taker_fee","perp_taker_fee","roundtrip_fee_bps",
    "funding_rate","screen_net_one_funding_bps","snapshot_gap_ms","fee_source"
]
CROSS_FIELDS = [
    "utc","kind","symbol","capital",
    "long_provider","short_provider","long_price","short_price",
    "gross_edge_bps","roundtrip_fee_bps","funding_diff_bps",
    "screen_net_bps","snapshot_gap_ms"
]
ERR_FIELDS = ["utc","provider","symbol","stage","error"]

def collect(minutes):
    providers = discover_all()
    if not providers:
        raise SystemExit("No reachable provider")

    same_path = OUT/"same_exchange_spot_perp.csv"
    cross_path = OUT/"cross_exchange.csv"
    err_path = OUT/"sample_errors.csv"

    with same_path.open("w", newline="", encoding="utf-8") as sf,          cross_path.open("w", newline="", encoding="utf-8") as cf,          err_path.open("w", newline="", encoding="utf-8") as ef:

        sw = csv.DictWriter(sf, fieldnames=SAME_FIELDS); sw.writeheader()
        cw = csv.DictWriter(cf, fieldnames=CROSS_FIELDS); cw.writeheader()
        ew = csv.DictWriter(ef, fieldnames=ERR_FIELDS); ew.writeheader()

        end = time.time() + minutes*60
        cycle = 0
        while time.time() < end:
            cycle += 1
            snap = {}

            for item in providers:
                name = item["name"]
                sx, dx = item["spot"], item["deriv"]
                fee = fees_for(name)

                for base in BASES:
                    p = item["pairs"][base]
                    try:
                        t0 = int(time.time()*1000)
                        sb = sx.fetch_order_book(p["spot"]["symbol"], 50)
                        t1 = int(time.time()*1000)
                        db = dx.fetch_order_book(p["swap"]["symbol"], 50)
                        t2 = int(time.time()*1000)
                        fr, _ = funding(dx, p["swap"]["symbol"])

                        snap[(name, base)] = {
                            "quote": p["quote"], "spot": sb, "perp": db,
                            "funding": fr, "ts": t2,
                            "spot_fee": fee["spot"], "perp_fee": fee["perp"],
                        }

                        for cap in CAPITALS:
                            sp = vwap(sb["asks"], cap)
                            pp = vwap(db["bids"], cap)
                            if sp is None or pp is None:
                                continue
                            raw = (pp/sp - 1.0)*10000
                            rt = 2*(fee["spot"]+fee["perp"])*10000
                            net = raw + fr*10000 - rt
                            sw.writerow({
                                "utc": now(),"provider":name,"symbol":base,"quote":p["quote"],"capital":cap,
                                "spot_buy_vwap":f"{sp:.10f}","perp_sell_vwap":f"{pp:.10f}",
                                "raw_basis_bps":f"{raw:.6f}","spot_taker_fee":fee["spot"],
                                "perp_taker_fee":fee["perp"],"roundtrip_fee_bps":f"{rt:.6f}",
                                "funding_rate":f"{fr:.12f}","screen_net_one_funding_bps":f"{net:.6f}",
                                "snapshot_gap_ms":max(0,t2-t1),"fee_source":fee["source"],
                            })
                        sf.flush()
                    except Exception as e:
                        ew.writerow({"utc":now(),"provider":name,"symbol":base,"stage":"sample",
                                     "error":f"{type(e).__name__}: {e}"[:800]})
                        ef.flush()

            # Cross-exchange screens using the snapshots from this cycle.
            for base in BASES:
                available = [(name, d) for (name,b),d in snap.items() if b == base]
                for (long_name,long_d),(short_name,short_d) in itertools.permutations(available,2):
                    if long_name == short_name:
                        continue
                    # Perp -> perp: long at ask on A, short at bid on B.
                    for cap in CAPITALS:
                        long_px = vwap(long_d["perp"]["asks"], cap)
                        short_px = vwap(short_d["perp"]["bids"], cap)
                        if long_px is not None and short_px is not None:
                            gross = (short_px/long_px - 1.0)*10000
                            rt = 2*(long_d["perp_fee"]+short_d["perp_fee"])*10000
                            fd = (short_d["funding"] - long_d["funding"])*10000
                            cw.writerow({
                                "utc":now(),"kind":"perp_perp","symbol":base,"capital":cap,
                                "long_provider":long_name,"short_provider":short_name,
                                "long_price":f"{long_px:.10f}","short_price":f"{short_px:.10f}",
                                "gross_edge_bps":f"{gross:.6f}","roundtrip_fee_bps":f"{rt:.6f}",
                                "funding_diff_bps":f"{fd:.6f}","screen_net_bps":f"{gross+fd-rt:.6f}",
                                "snapshot_gap_ms":abs(long_d["ts"]-short_d["ts"]),
                            })
                        # Spot A -> short perp B.
                        spot_px = vwap(long_d["spot"]["asks"], cap)
                        perp_bid = vwap(short_d["perp"]["bids"], cap)
                        if spot_px is not None and perp_bid is not None:
                            gross = (perp_bid/spot_px - 1.0)*10000
                            rt = 2*(long_d["spot_fee"]+short_d["perp_fee"])*10000
                            fd = short_d["funding"]*10000
                            cw.writerow({
                                "utc":now(),"kind":"spot_perp_cross","symbol":base,"capital":cap,
                                "long_provider":long_name,"short_provider":short_name,
                                "long_price":f"{spot_px:.10f}","short_price":f"{perp_bid:.10f}",
                                "gross_edge_bps":f"{gross:.6f}","roundtrip_fee_bps":f"{rt:.6f}",
                                "funding_diff_bps":f"{fd:.6f}","screen_net_bps":f"{gross+fd-rt:.6f}",
                                "snapshot_gap_ms":abs(long_d["ts"]-short_d["ts"]),
                            })
                    cf.flush()

            print(now(), "cycle", cycle, "providers", ",".join(sorted({x["name"] for x in providers})), flush=True)
            time.sleep(5)

def summarize_file(path, key_fields, value_field, out_name):
    if not path.exists():
        return
    groups = {}
    with path.open("r",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            try:
                key = tuple(r[k] for k in key_fields)
                groups.setdefault(key,[]).append(float(r[value_field]))
            except Exception:
                pass
    out = OUT/out_name
    fields = key_fields + ["n","positive_pct","p05_bps","median_bps","mean_bps","p95_bps","p99_bps","max_bps"]
    with out.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for key,xs in sorted(groups.items()):
            n=len(xs)
            row=dict(zip(key_fields,key))
            row.update({
                "n":n,"positive_pct":round(100*sum(x>0 for x in xs)/n,4),
                "p05_bps":round(pct(xs,.05),6),"median_bps":round(statistics.median(xs),6),
                "mean_bps":round(statistics.fmean(xs),6),"p95_bps":round(pct(xs,.95),6),
                "p99_bps":round(pct(xs,.99),6),"max_bps":round(max(xs),6),
            })
            w.writerow(row)

def summarize():
    summarize_file(OUT/"same_exchange_spot_perp.csv",
                   ["provider","symbol","quote","capital"],
                   "screen_net_one_funding_bps","same_exchange_summary.csv")
    summarize_file(OUT/"cross_exchange.csv",
                   ["kind","symbol","capital","long_provider","short_provider"],
                   "screen_net_bps","cross_exchange_summary.csv")

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--minutes",type=float,default=3.0)
    args=ap.parse_args()
    collect(args.minutes)
    summarize()
