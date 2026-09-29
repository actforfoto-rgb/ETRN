from __future__ import annotations

import argparse, csv, json, math, statistics, time
from datetime import datetime, timezone
from pathlib import Path
import ccxt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"
OUT.mkdir(parents=True, exist_ok=True)

BASES = ["BTC", "ETH"]
CAPITALS = [100.0, 1000.0, 10000.0]
QUOTES = ["USDT", "USD"]

# Standard taker assumptions used only for screening.
# These are stored explicitly so raw observations can be re-priced later.
FEES = {
    "BITGET": {"spot": 0.0010, "perp": 0.0006},
    "OKX": {"spot": 0.0010, "perp": 0.0005},
    "KRAKEN": {"spot": 0.0080, "perp": 0.0005},
    "GATE": {"spot": 0.0010, "perp": 0.0005}, # VIP0 official baseline
    "BYBIT": {"spot": 0.0010, "perp": 0.00055},
    "BINANCE": {"spot": 0.0010, "perp": 0.0005},
    "MEXC": {"spot": 0.0005, "perp": 0.0008},
    "HTX": {"spot": 0.0020, "perp": 0.0005},
}

def utc():
    return datetime.now(timezone.utc).isoformat()

def vwap(levels, quote_amount):
    rem = quote_amount
    base = done = 0.0
    for level in levels:
        px = float(level[0]); qty = float(level[1])
        q = px * qty
        take = min(rem, q)
        done += take
        base += take / px
        rem -= take
        if rem <= 1e-9:
            break
    if rem > 1e-6 or base <= 0:
        return None
    return done / base

def normalize_deriv_levels(levels, market):
    cs = float(market.get("contractSize") or 1.0)
    if not market.get("contract"):
        cs = 1.0
    return [(float(level[0]), float(level[1]) * cs) for level in levels]

def choose_pair(ex, base, quote):
    spots = [m for m in ex.markets.values() if m.get("spot") and m.get("active", True)
             and m.get("base") == base and m.get("quote") == quote]
    swaps = [m for m in ex.markets.values() if m.get("swap") and m.get("active", True)
             and m.get("base") == base and m.get("quote") == quote]
    if not spots or not swaps:
        return None
    linear = [m for m in swaps if m.get("linear")]
    return spots[0], (linear or swaps)[0]

def build_same(exid):
    cls = getattr(ccxt, exid)
    ex = cls({"enableRateLimit": True, "timeout": 15000})
    ex.load_markets()
    pairs = {}
    for base in BASES:
        pair = None
        for q in QUOTES:
            pair = choose_pair(ex, base, q)
            if pair:
                spot, swap = pair
                pairs[base] = (spot, swap)
                break
        if base not in pairs:
            raise RuntimeError(f"no spot+swap pair for {base}")
    return exid.upper(), ex, ex, pairs

def build_kraken():
    sx = ccxt.kraken({"enableRateLimit": True, "timeout": 15000})
    dx = ccxt.krakenfutures({"enableRateLimit": True, "timeout": 15000})
    sx.load_markets(); dx.load_markets()
    pairs = {}
    for base in BASES:
        spots = [m for m in sx.markets.values() if m.get("spot") and m.get("active", True)
                 and m.get("base") == base and m.get("quote") == "USD"]
        swaps = [m for m in dx.markets.values() if m.get("swap") and m.get("active", True)
                 and m.get("base") == base and m.get("quote") == "USD"]
        if not spots or not swaps:
            raise RuntimeError(f"Kraken no {base}/USD spot+swap")
        pairs[base] = (spots[0], swaps[0])
    return "KRAKEN", sx, dx, pairs

def funding(ex, symbol):
    try:
        if ex.has.get("fetchFundingRate"):
            r = ex.fetch_funding_rate(symbol)
            return float(r.get("fundingRate") or 0.0), r.get("fundingTimestamp")
    except Exception:
        pass
    return 0.0, None

def discover():
    specs = [("okx", build_same), ("bitget", build_same), ("gate", build_same),
             ("mexc", build_same), ("htx", build_same),
             ("bybit", build_same), ("binance", build_same)]
    providers = []
    cov = []
    for exid, fn in specs:
        t0 = time.time()
        try:
            name, sx, dx, pairs = fn(exid)
            # real probe
            s, d = pairs["BTC"]
            sx.fetch_order_book(s["symbol"], 20)
            dx.fetch_order_book(d["symbol"], 20)
            providers.append((name, sx, dx, pairs))
            cov.append({"provider": name, "ok": True, "seconds": round(time.time()-t0,3)})
        except Exception as e:
            cov.append({"provider": exid.upper(), "ok": False, "seconds": round(time.time()-t0,3),
                        "error": f"{type(e).__name__}: {e}"[:700]})
    t0 = time.time()
    try:
        item = build_kraken()
        name, sx, dx, pairs = item
        s, d = pairs["BTC"]
        sx.fetch_order_book(s["symbol"], 20)
        dx.fetch_order_book(d["symbol"], 20)
        providers.append(item)
        cov.append({"provider": name, "ok": True, "seconds": round(time.time()-t0,3)})
    except Exception as e:
        cov.append({"provider":"KRAKEN","ok":False,"seconds":round(time.time()-t0,3),
                    "error":f"{type(e).__name__}: {e}"[:700]})
    (OUT/"crypto_c02_coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    return providers

SP_FIELDS = ["utc","provider","base","quote","capital","spot_ask_vwap","perp_bid_vwap",
             "gross_basis_bps","funding_rate","funding_bps","roundtrip_fee_bps",
             "net_one_funding_bps","funding_intervals_to_breakeven",
             "spot_symbol","perp_symbol","contract_size","sample_ms"]

CP_FIELDS = ["utc","base","quote","capital","long_provider","short_provider",
             "long_perp_ask_vwap","short_perp_bid_vwap","gross_cross_basis_bps",
             "long_funding_rate","short_funding_rate","funding_capture_bps",
             "roundtrip_fee_bps","screen_net_bps","funding_intervals_to_breakeven"]

def collect(minutes):
    providers = discover()
    if not providers:
        raise SystemExit("No reachable provider")
    sp_path = OUT/"crypto_c02_spot_perp.csv"
    cp_path = OUT/"crypto_c02_cross_perp.csv"
    with sp_path.open("w", newline="", encoding="utf-8") as sf, cp_path.open("w", newline="", encoding="utf-8") as cf:
        sw = csv.DictWriter(sf, fieldnames=SP_FIELDS); sw.writeheader()
        cw = csv.DictWriter(cf, fieldnames=CP_FIELDS); cw.writeheader()
        end = time.time() + minutes*60
        while time.time() < end:
            snapshots = {}
            for name, sx, dx, pairs in providers:
                for base in BASES:
                    try:
                        spot_m, perp_m = pairs[base]
                        t0 = time.time()
                        limit = 20 if name == "HTX" else 50
                        sb = sx.fetch_order_book(spot_m["symbol"], limit)
                        db = dx.fetch_order_book(perp_m["symbol"], limit)
                        fr, _ = funding(dx, perp_m["symbol"])
                        ms = int((time.time()-t0)*1000)
                        asks = [(float(p),float(q)) for p,q in sb["asks"]]
                        bids = normalize_deriv_levels(db["bids"], perp_m)
                        dasks = normalize_deriv_levels(db["asks"], perp_m)
                        quote = spot_m["quote"]
                        fee = FEES.get(name, {"spot":0.001, "perp":0.0006})
                        rt = 2*(fee["spot"]+fee["perp"])*10000
                        snapshots[(name,base)] = {
                            "quote":quote, "perp_bids":bids, "perp_asks":dasks,
                            "funding":fr, "perp_fee":fee["perp"]
                        }
                        for cap in CAPITALS:
                            sp = vwap(asks, cap)
                            pp = vwap(bids, cap)
                            if sp is None or pp is None: continue
                            gross = (pp/sp-1)*10000
                            sw.writerow({
                                "utc":utc(),"provider":name,"base":base,"quote":quote,"capital":cap,
                                "spot_ask_vwap":f"{sp:.10f}","perp_bid_vwap":f"{pp:.10f}",
                                "gross_basis_bps":f"{gross:.6f}","funding_rate":f"{fr:.12f}",
                                "funding_bps":f"{fr*10000:.6f}",
                                "roundtrip_fee_bps":f"{rt:.6f}",
                                "net_one_funding_bps":f"{gross+fr*10000-rt:.6f}",
                                "funding_intervals_to_breakeven":(
                                    "" if fr <= 0 else f"{max(0.0,(rt-gross)/(fr*10000)):.4f}"
                                ),
                                "spot_symbol":spot_m["symbol"],"perp_symbol":perp_m["symbol"],
                                "contract_size":perp_m.get("contractSize") or 1.0,"sample_ms":ms
                            })
                        sf.flush()
                    except Exception as e:
                        with (OUT/"crypto_c02_errors.log").open("a",encoding="utf-8") as ef:
                            ef.write(f"{utc()} {name} {base} {type(e).__name__}: {e}\n")
            # cross-perpetual within same quote only
            for base in BASES:
                items = [(n,s) for (n,b),s in snapshots.items() if b==base]
                for i in range(len(items)):
                    for j in range(len(items)):
                        if i==j: continue
                        long_name, L = items[i]; short_name, S = items[j]
                        if L["quote"] != S["quote"]: continue
                        for cap in CAPITALS:
                            lp = vwap(L["perp_asks"], cap)
                            sp = vwap(S["perp_bids"], cap)
                            if lp is None or sp is None: continue
                            gross=(sp/lp-1)*10000
                            rt=2*(L["perp_fee"]+S["perp_fee"])*10000
                            # Funding sign convention: long pays positive funding; short receives positive funding.
                            fund = (-L["funding"] + S["funding"]) * 10000
                            be = "" if fund <= 0 else f"{max(0.0,(rt-gross)/fund):.4f}"
                            cw.writerow({
                                "utc":utc(),"base":base,"quote":L["quote"],"capital":cap,
                                "long_provider":long_name,"short_provider":short_name,
                                "long_perp_ask_vwap":f"{lp:.10f}","short_perp_bid_vwap":f"{sp:.10f}",
                                "gross_cross_basis_bps":f"{gross:.6f}",
                                "long_funding_rate":f"{L['funding']:.12f}",
                                "short_funding_rate":f"{S['funding']:.12f}",
                                "funding_capture_bps":f"{fund:.6f}",
                                "roundtrip_fee_bps":f"{rt:.6f}",
                                "screen_net_bps":f"{gross+fund-rt:.6f}",
                                "funding_intervals_to_breakeven":be
                            })
            cf.flush()
            time.sleep(5)

def summarize():
    for src, metric, name in [
        ("crypto_c02_spot_perp.csv","net_one_funding_bps","crypto_c02_spot_perp_summary.csv"),
        ("crypto_c02_cross_perp.csv","screen_net_bps","crypto_c02_cross_perp_summary.csv")
    ]:
        p=OUT/src
        if not p.exists(): continue
        groups={}
        with p.open("r",encoding="utf-8",newline="") as f:
            for r in csv.DictReader(f):
                try:
                    if "provider" in r:
                        k=(r["provider"],r["base"],r["quote"],r["capital"])
                    else:
                        k=(r["long_provider"]+"->"+r["short_provider"],r["base"],r["quote"],r["capital"])
                    groups.setdefault(k,[]).append(float(r[metric]))
                except: pass
        out=OUT/name
        fields=["route","base","quote","capital","n","positive_pct","median_bps","p95_bps","max_bps"]
        with out.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for k,xs in sorted(groups.items()):
                route,base,q,cap=k;n=len(xs);ys=sorted(xs)
                p95=ys[int(round((n-1)*.95))]
                w.writerow({"route":route,"base":base,"quote":q,"capital":cap,"n":n,
                            "positive_pct":round(100*sum(x>0 for x in xs)/n,3),
                            "median_bps":round(statistics.median(xs),6),
                            "p95_bps":round(p95,6),"max_bps":round(max(xs),6)})

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--minutes",type=float,default=1.0)
    a=ap.parse_args();collect(a.minutes);summarize()
