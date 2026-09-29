from __future__ import annotations

import csv, json, math, statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"
OUT.mkdir(parents=True, exist_ok=True)

S = requests.Session()
S.headers.update({"User-Agent": "NORM-LAB-ARBITRAGE-RADAR/1.0"})
BASE = "https://iss.moex.com/iss"
TODAY = datetime.now(ZoneInfo("Europe/Moscow")).date()

MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}
THRESHOLDS = [1.0, 1.10, 1.25, 1.50, 2.0, 2.5, 3.0]
MIN_DAYS_TO_EXPIRY = 5
MAX_DAYS_TO_EXPIRY = 90

# Deterministic FORTS SECID prefixes remove ambiguous search aliases.
FAMILIES = [
    {"name": "CNY", "perp": "CNYRUBF", "quarter_prefix": "CR", "proxy": "CRZ6"},
    {"name": "USD", "perp": "USDRUBF", "quarter_prefix": "Si", "proxy": "SiZ6"},
    {"name": "EUR", "perp": "EURRUBF", "quarter_prefix": "Eu", "proxy": "EuZ6"},
    {"name": "IMOEX", "perp": "IMOEXF", "quarter_prefix": "MM", "proxy": "MMZ6"},
]

YEARS = [2025, 2026]


def get(url, params=None):
    r = S.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def rows(j, name):
    b = j.get(name, {})
    cols = b.get("columns", [])
    return [dict(zip(cols, x)) for x in b.get("data", [])]


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def secid_for(prefix, year, month):
    return f"{prefix}{MONTH_CODE[month]}{str(year)[-1]}"


def current_meta(secid):
    j = get(
        f"{BASE}/engines/futures/markets/forts/boards/RFUD/securities/{secid}.json",
        {"iss.meta": "off"},
    )
    s = rows(j, "securities")
    m = rows(j, "marketdata")
    if not s:
        return None
    a = s[0]
    b = m[0] if m else {}
    minstep = num(a.get("MINSTEP")) or 1.0
    stepprice = num(a.get("STEPPRICE")) or minstep
    return {
        "secid": secid,
        "unit": stepprice / minstep,
        "im": num(a.get("INITIALMARGIN")) or 0.0,
        "fee": num(a.get("BUYSELLFEE")) or 0.0,
        "bid": num(b.get("BID")),
        "ask": num(b.get("OFFER")),
        "expiry": a.get("LASTTRADEDATE"),
    }


def hist(secid, frm="2025-01-01", till=None):
    if till is None:
        till = TODAY.isoformat()
    url = (
        f"{BASE}/history/engines/futures/markets/forts/boards/RFUD/"
        f"securities/{secid}.json"
    )
    out = []
    start = 0
    while True:
        j = get(
            url,
            {
                "from": frm,
                "till": till,
                "start": start,
                "iss.meta": "off",
            },
        )
        rr = rows(j, "history")
        out.extend(rr)
        cur = rows(j, "history.cursor")
        total = int(cur[0].get("TOTAL") or 0) if cur else len(out)
        if not rr or len(out) >= total:
            break
        start = len(out)
    return out


def settle(r):
    for k in ("SETTLEPRICE", "CLOSE", "WAPRICE"):
        v = num(r.get(k))
        if v is not None:
            return v
    return None


def swap(r):
    return num(r.get("SWAPRATE") or r.get("SWAPRATE_CURR"))


def cbr_key_rates():
    url = "https://www.cbr.ru/hd_base/KeyRate/"
    params = {
        "UniDbQuery.Posted": "True",
        "UniDbQuery.From": "01.01.2025",
        "UniDbQuery.To": TODAY.strftime("%d.%m.%Y"),
    }
    r = S.get(url, params=params, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = {}
    for tr in soup.select("table.data tbody tr"):
        tds = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
        if len(tds) < 2:
            continue
        try:
            d = datetime.strptime(tds[0], "%d.%m.%Y").date().isoformat()
            out[d] = float(tds[1].replace(",", "."))
        except Exception:
            pass
    if not out:
        raise RuntimeError("CBR key-rate table is empty")
    return out


def fallback_key_rate(d):
    # Used only if the CBR page is temporarily unavailable.
    regimes = [
        ("2025-01-01", 21.0),
        ("2025-06-09", 20.0),
        ("2025-07-28", 18.0),
        ("2025-09-15", 17.0),
        ("2025-10-27", 16.5),
        ("2025-12-22", 16.0),
        ("2026-02-16", 15.5),
        ("2026-03-23", 15.0),
        ("2026-04-27", 14.5),
        ("2026-06-22", 14.25),
        ("2026-07-27", 14.0),
    ]
    v = regimes[0][1]
    for start, rate in regimes:
        if d >= start:
            v = rate
    return v / 100.0


def rate_on(rate_map, d):
    if rate_map:
        eligible = [x for x in rate_map if x <= d]
        if eligible:
            return rate_map[max(eligible)] / 100.0
    return fallback_key_rate(d)


def capital_cost(im, start, end, rate_map, multiplier=1.0):
    a = date.fromisoformat(start)
    b = date.fromisoformat(end)
    cur = a
    cost = 0.0
    while cur < b:
        cost += im * rate_on(rate_map, cur.isoformat()) / 365.0 * multiplier
        cur += timedelta(days=1)
    return cost


def business_days(a, b):
    n = 0
    cur = a
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def infer_scale(q, p):
    cands = [0.001, 0.01, 0.1, 1, 10, 100, 1000, 10000]
    return min(cands, key=lambda s: abs(math.log(max(1e-12, (q / s) / p))))


def completed_cycle(year, month, quarter_sid, fam, pmeta, qmeta, rate_map):
    qh = hist(quarter_sid, frm=f"{year}-01-01", till=TODAY.isoformat())
    ph = hist(fam["perp"], frm=f"{year}-01-01", till=TODAY.isoformat())

    q = {r.get("TRADEDATE"): r for r in qh if r.get("TRADEDATE")}
    p = {r.get("TRADEDATE"): r for r in ph if r.get("TRADEDATE")}
    common = sorted(set(q) & set(p))
    if len(common) < 3:
        return None

    last = date.fromisoformat(common[-1])

    # A completed quarterly contract must actually have stopped before today,
    # and its last history month must correspond to the contract month.
    if last >= TODAY or last.year != year or last.month != month:
        return None

    exit_date = common[-1]
    q_exit = settle(q[exit_date])
    p_exit = settle(p[exit_date])
    if q_exit is None or p_exit is None:
        return None

    first = common[0]
    q0 = settle(q[first])
    p0 = settle(p[first])
    if q0 is None or p0 is None:
        return None

    scale = infer_scale(q0, p0)
    q_sens = qmeta["unit"] * scale
    p_sens = pmeta["unit"]
    q_qty = p_sens / q_sens

    combined_im = q_qty * qmeta["im"] + pmeta["im"]
    fees = 2 * (q_qty * qmeta["fee"] + pmeta["fee"])

    # Use current full spreads only as a conservative execution proxy.
    spread_proxy = 0.0
    if qmeta["bid"] is not None and qmeta["ask"] is not None:
        spread_proxy += (
            (qmeta["ask"] - qmeta["bid"]) * q_qty * qmeta["unit"]
        )
    if pmeta["bid"] is not None and pmeta["ask"] is not None:
        spread_proxy += (pmeta["ask"] - pmeta["bid"]) * pmeta["unit"]

    entries = []
    for d in common[:-1]:
        q_entry = settle(q[d])
        p_entry = settle(p[d])
        sw = swap(p[d])
        if q_entry is None or p_entry is None or sw is None:
            continue

        days = (date.fromisoformat(exit_date) - date.fromisoformat(d)).days
        work = business_days(date.fromisoformat(d), date.fromisoformat(exit_date))
        if days < MIN_DAYS_TO_EXPIRY or days > MAX_DAYS_TO_EXPIRY or work <= 0:
            continue

        carry = capital_cost(combined_im, d, exit_date, rate_map, 1.0)

        if sw >= 0:
            direction = "LONG_QUARTERLY_SHORT_PERP"
            entry_gap = (
                q_entry * q_qty * qmeta["unit"] - p_entry * pmeta["unit"]
            )
            q_pnl = (q_exit - q_entry) * q_qty * qmeta["unit"]
            p_pnl = (p_entry - p_exit) * pmeta["unit"]
            funding = sum(
                (swap(p[x]) or 0.0) * pmeta["unit"]
                for x in common
                if d < x <= exit_date
            )
        else:
            direction = "SHORT_QUARTERLY_LONG_PERP"
            entry_gap = (
                p_entry * pmeta["unit"] - q_entry * q_qty * qmeta["unit"]
            )
            q_pnl = (q_entry - q_exit) * q_qty * qmeta["unit"]
            p_pnl = (p_exit - p_entry) * pmeta["unit"]
            funding = -sum(
                (swap(p[x]) or 0.0) * pmeta["unit"]
                for x in common
                if d < x <= exit_date
            )

        # Base requirement uses 2x current full-spread proxy.
        exec_buffer = 2.0 * spread_proxy
        required = (
            entry_gap + carry + fees + exec_buffer
        ) / (pmeta["unit"] * work)
        ratio = abs(sw) / required if required > 0 else 99.0

        net = q_pnl + p_pnl + funding - carry - fees - exec_buffer

        # Stress: 1.5x capital opportunity cost and 2x execution buffer again.
        stress_carry = capital_cost(combined_im, d, exit_date, rate_map, 1.5)
        stress_exec = 4.0 * spread_proxy
        stress_net = (
            q_pnl + p_pnl + funding - stress_carry - fees - stress_exec
        )

        entries.append(
            {
                "family": fam["name"],
                "year": year,
                "month": month,
                "cycle": f"{fam['name']}-{year}-{month:02d}",
                "quarter": quarter_sid,
                "entry": d,
                "exit": exit_date,
                "days": days,
                "business_days": work,
                "direction": direction,
                "entry_swaprate": sw,
                "required_swaprate": required,
                "swaprate_ratio": ratio,
                "entry_gap_rub": entry_gap,
                "quarter_pnl_rub": q_pnl,
                "perp_pnl_rub": p_pnl,
                "funding_rub": funding,
                "capital_cost_rub": carry,
                "fees_rub": fees,
                "execution_buffer_rub": exec_buffer,
                "net_rub": net,
                "return_on_im_pct": net / combined_im * 100 if combined_im else None,
                "stress_net_rub": stress_net,
                "stress_return_on_im_pct": (
                    stress_net / combined_im * 100 if combined_im else None
                ),
                "combined_im_rub": combined_im,
            }
        )

    return {
        "cycle": f"{fam['name']}-{year}-{month:02d}",
        "family": fam["name"],
        "year": year,
        "month": month,
        "quarter": quarter_sid,
        "exit": exit_date,
        "entries": entries,
    }


def first_crossing(cycle, threshold):
    for r in sorted(cycle["entries"], key=lambda x: x["entry"]):
        if r["swaprate_ratio"] >= threshold:
            return r
    return None


def summarize(selected, family, threshold, split):
    xs = [x for x in selected if x["family"] == family]
    if not xs:
        return None
    nets = [x["return_on_im_pct"] for x in xs]
    stress = [x["stress_return_on_im_pct"] for x in xs]
    return {
        "family": family,
        "ratio_threshold": threshold,
        "split": split,
        "n_cycles": len(xs),
        "positive_cycles_pct": 100 * sum(x > 0 for x in nets) / len(nets),
        "median_return_on_im_pct": statistics.median(nets),
        "mean_return_on_im_pct": statistics.fmean(nets),
        "aggregate_net_rub": sum(x["net_rub"] for x in xs),
        "worst_return_on_im_pct": min(nets),
        "best_return_on_im_pct": max(nets),
        "stress_positive_cycles_pct": (
            100 * sum(x > 0 for x in stress) / len(stress)
        ),
        "stress_median_return_on_im_pct": statistics.median(stress),
        "stress_aggregate_net_rub": sum(x["stress_net_rub"] for x in xs),
        "cycles": [x["cycle"] for x in xs],
    }


def main():
    try:
        rate_map = cbr_key_rates()
        rate_source = "CBR"
    except Exception as e:
        rate_map = {}
        rate_source = f"fallback:{type(e).__name__}"

    cycles = []
    resolution = []

    for fam in FAMILIES:
        pmeta = current_meta(fam["perp"])
        proxy = current_meta(fam["proxy"])
        if not pmeta or not proxy:
            resolution.append(
                {"family": fam["name"], "error": "perp/proxy metadata missing"}
            )
            continue

        for year in YEARS:
            for month in (3, 6, 9, 12):
                sid = secid_for(fam["quarter_prefix"], year, month)
                qmeta = current_meta(sid)
                metadata_proxy = False
                if not qmeta or qmeta["im"] <= 0:
                    qmeta = dict(proxy)
                    qmeta["secid"] = sid
                    metadata_proxy = True

                try:
                    cyc = completed_cycle(
                        year, month, sid, fam, pmeta, qmeta, rate_map
                    )
                except Exception as e:
                    resolution.append(
                        {
                            "family": fam["name"],
                            "year": year,
                            "month": month,
                            "sid": sid,
                            "error": f"{type(e).__name__}: {e}",
                        }
                    )
                    continue

                if cyc is None:
                    resolution.append(
                        {
                            "family": fam["name"],
                            "year": year,
                            "month": month,
                            "sid": sid,
                            "status": "not_completed_or_no_history",
                            "metadata_proxy": metadata_proxy,
                        }
                    )
                    continue

                cycles.append(cyc)
                resolution.append(
                    {
                        "family": fam["name"],
                        "year": year,
                        "month": month,
                        "sid": sid,
                        "status": "completed",
                        "exit": cyc["exit"],
                        "entry_candidates": len(cyc["entries"]),
                        "metadata_proxy": metadata_proxy,
                    }
                )

    # Persist every candidate date for forensic use.
    all_entries = [
        x for cyc in cycles for x in cyc["entries"]
    ]
    if all_entries:
        with (OUT / "moex_cycle_all_candidate_entries.csv").open(
            "w", newline="", encoding="utf-8"
        ) as f:
            w = csv.DictWriter(f, fieldnames=list(all_entries[0].keys()))
            w.writeheader()
            w.writerows(all_entries)

    # One and only one trade per completed expiry cycle: first threshold crossing.
    first_trades = []
    summary = []

    for th in THRESHOLDS:
        selected = []
        for cyc in cycles:
            r = first_crossing(cyc, th)
            if r is not None:
                r = dict(r)
                r["ratio_threshold"] = th
                selected.append(r)
                first_trades.append(r)

        for fam in FAMILIES:
            name = fam["name"]
            for split, predicate in (
                ("ALL", lambda x: True),
                ("TRAIN_2025", lambda x: x["year"] == 2025),
                ("HOLDOUT_2026", lambda x: x["year"] == 2026),
            ):
                block = [x for x in selected if predicate(x)]
                row = summarize(block, name, th, split)
                if row:
                    summary.append(row)

    if first_trades:
        with (OUT / "moex_cycle_first_trigger_trades.csv").open(
            "w", newline="", encoding="utf-8"
        ) as f:
            w = csv.DictWriter(f, fieldnames=list(first_trades[0].keys()))
            w.writeheader()
            w.writerows(first_trades)

    (OUT / "moex_cycle_resolution_v2.json").write_text(
        json.dumps(
            {
                "today_moscow": TODAY.isoformat(),
                "key_rate_source": rate_source,
                "resolution": resolution,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (OUT / "moex_cycle_first_trigger_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Compact leaderboard: ALL + holdout positive under base and stress.
    leaderboard = [
        x
        for x in summary
        if x["split"] in ("ALL", "HOLDOUT_2026")
        and x["median_return_on_im_pct"] > 0
        and x["stress_median_return_on_im_pct"] > 0
    ]
    leaderboard.sort(
        key=lambda x: (
            x["split"] == "HOLDOUT_2026",
            x["stress_median_return_on_im_pct"],
            x["n_cycles"],
        ),
        reverse=True,
    )
    (OUT / "moex_cycle_leaderboard.json").write_text(
        json.dumps(leaderboard, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "completed_cycles": len(cycles),
                "rate_source": rate_source,
                "leaderboard": leaderboard[:30],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
