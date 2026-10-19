#!/usr/bin/env python3
"""NORM 03B: strictly read-only PUBLIC Kraken v2 capture and conservative M6 screen.

No authentication, orders, client eligibility inference, secret handling or exports of raw
market messages. A model trade-through is NOT a broker fill. Only summary is publishable.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import zlib

WS_URL = "wss://ws.kraken.com/v2"
WHITELIST = {"BTC/USD", "ETH/USD", "SOL/USD"}
DEPTH = 10
MAX_SECONDS = 180
MAX_BYTES = 8_000_000
MAX_PACKETS = 15000
SAMPLE_NS = 30_000_000_000
LATENCY_NS = 300_000_000
HOLD_NS = 10_000_000_000
MAX_WAIT_NS = 75_000_000_000
BOOK_STALE_NS = 3_000_000_000
NOTIONAL = Decimal("100")  # scenario USD, explicitly not RUB portfolio
MAKER_FEE = Decimal("0.0038")  # public fee scenario; NOT account-confirmed
TAKER_FEE = Decimal("0.0080")  # public fee scenario; NOT account-confirmed
CASH_APR = Decimal("0.14")  # illustrative, not a verified USD yield


def utc_ns() -> int:
    return time.time_ns()


def iso_ns(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).isoformat()


def parse_ns(value: str) -> int:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("bad exchange timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive exchange timestamp")
    return int(dt.timestamp() * 1_000_000_000)


def dec(v: object) -> Decimal:
    x = Decimal(str(v))
    if not x.is_finite():
        raise ValueError("nonfinite number")
    return x


def canonical_num(x: Decimal) -> str:
    return format(x, "f").replace(".", "").lstrip("0") or "0"


def crc_book(bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal]) -> int:
    data = "".join(canonical_num(p) + canonical_num(q) for p, q in sorted(asks.items())[:10])
    data += "".join(canonical_num(p) + canonical_num(q) for p, q in sorted(bids.items(), reverse=True)[:10])
    return zlib.crc32(data.encode("ascii")) & 0xFFFFFFFF


@dataclass
class Book:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    ready: bool = False
    updated_ns: int = 0
    exchange_ns: int = 0
    mismatch: int = 0
    received_updates: int = 0

    def apply(self, item: dict, kind: str, received_ns: int) -> bool:
        """Apply each packet atomically. CRC failure invalidates book until fresh snapshot."""
        if kind == "snapshot":
            bids, asks = {}, {}
        elif kind == "update" and self.ready:
            bids, asks = self.bids.copy(), self.asks.copy()
        else:
            return False
        try:
            for attr, bucket in (("bids", bids), ("asks", asks)):
                for level in item.get(attr, []):
                    p, q = dec(level["price"]), dec(level["qty"])
                    if p <= 0 or q < 0:
                        raise ValueError("invalid book level")
                    if q == 0:
                        bucket.pop(p, None)
                    else:
                        bucket[p] = q
            bids = dict(sorted(bids.items(), reverse=True)[:DEPTH])
            asks = dict(sorted(asks.items())[:DEPTH])
            checksum = int(item["checksum"])
            ts = parse_ns(item["timestamp"])
            if not bids or not asks or max(bids) >= min(asks) or crc_book(bids, asks) != checksum:
                raise ValueError("bad book checksum or crossed book")
            # Guard against network timestamp anomalies; never treat negative-age as causal.
            if ts > received_ns + 1_000_000_000 or received_ns - ts > 10_000_000_000:
                raise ValueError("inconsistent exchange timestamp")
        except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
            self.ready = False
            self.mismatch += 1
            self.bids.clear()
            self.asks.clear()
            return False
        self.bids, self.asks = bids, asks
        self.ready, self.updated_ns, self.exchange_ns = True, received_ns, ts
        self.received_updates += 1
        return True

    def bid(self) -> Decimal | None:
        return max(self.bids) if self.ready and self.bids else None

    def ask(self) -> Decimal | None:
        return min(self.asks) if self.ready and self.asks else None

    def valid_at(self, ns: int) -> bool:
        return self.ready and 0 <= ns - self.updated_ns <= BOOK_STALE_NS

    def sell_vwap(self, qty: Decimal) -> Decimal | None:
        rem, received = qty, Decimal(0)
        for price, depth in sorted(self.bids.items(), reverse=True):
            fill = min(rem, depth)
            rem -= fill
            received += fill * price
            if rem <= 0:
                return received / qty
        return None


@dataclass
class Episode:
    event_id: str
    symbol: str
    created_ns: int
    limit: Decimal
    qty: Decimal
    state: str = "LATENCY_PENDING"
    posted_ns: int = 0
    fill_ns: int = 0
    exchange_fill_ns: int = 0
    exit_ns: int = 0
    queue_ahead: Decimal = Decimal(0)
    net: Decimal | None = None
    gross: Decimal | None = None
    fee: Decimal | None = None
    opportunity_cost: Decimal | None = None


class Screen:
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.books = {s: Book() for s in symbols}
        self.episodes: dict[str, Episode | None] = {s: None for s in symbols}
        self.last_sample_ns = {s: 0 for s in symbols}
        self.completed: list[Episode] = []
        self.seen_trade_id: dict[str, set[int]] = {s: set() for s in symbols}
        self.counts: Counter[str] = Counter()
        self.first_receive_ns = 0
        self.last_receive_ns = 0
        self.causal_error = False

    def process(self, raw: str, received_ns: int) -> None:
        if received_ns < self.last_receive_ns:
            self.causal_error = True
            self.counts["out_of_order_receive"] += 1
            return
        self.last_receive_ns = received_ns
        self.first_receive_ns = self.first_receive_ns or received_ns
        try:
            message = json.loads(raw, parse_float=Decimal)
            if not isinstance(message, dict):
                return
            channel, typ = message.get("channel"), message.get("type")
            for item in message.get("data", []):
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol")
                if symbol not in self.books:
                    continue
                if channel == "book" and typ in ("snapshot", "update"):
                    book = self.books[symbol]
                    if book.apply(item, typ, received_ns):
                        self.counts["validated_books"] += 1
                        self.on_book(symbol, received_ns)
                    else:
                        self.counts["invalid_books"] += 1
                        self.invalidate_episode(symbol)
                elif channel == "trade" and typ == "update":
                    self.on_trade(symbol, item, received_ns)
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, InvalidOperation, OverflowError):
            self.counts["malformed_frame"] += 1

    def invalidate_episode(self, symbol: str) -> None:
        ep = self.episodes[symbol]
        if ep and ep.state not in ("CLOSED_PROXY", "MISSED", "INVALID_BOOK"):
            ep.state = "INVALID_BOOK"
            self.completed.append(ep)
            self.episodes[symbol] = None

    def on_book(self, symbol: str, ns: int) -> None:
        book = self.books[symbol]
        ep = self.episodes[symbol]
        if ep:
            if ep.state == "LATENCY_PENDING" and ns >= ep.created_ns + LATENCY_NS:
                if not book.valid_at(ns) or book.ask() <= ep.limit:
                    ep.state = "MISSED"
                    self.completed.append(ep)
                    self.episodes[symbol] = None
                else:
                    ep.state = "POSTED_PROXY"
                    ep.posted_ns = ns
                    ep.queue_ahead = book.bids.get(ep.limit, Decimal(0))
            elif ep.state == "POSTED_PROXY" and ns - ep.posted_ns > MAX_WAIT_NS:
                ep.state = "MISSED"
                self.completed.append(ep)
                self.episodes[symbol] = None
            elif ep.state == "FILLED_PROXY" and ns - ep.fill_ns >= HOLD_NS:
                bid = book.bid()
                vwap = book.sell_vwap(ep.qty)
                if bid is None or vwap is None or not book.valid_at(ns):
                    ep.state = "EXIT_DEPTH_UNAVAILABLE"
                else:
                    gross = (vwap - ep.limit) * ep.qty
                    fees = MAKER_FEE * ep.limit * ep.qty + TAKER_FEE * vwap * ep.qty
                    held_years = Decimal(ns - ep.fill_ns) / Decimal(1_000_000_000 * 86400 * 365)
                    cash_cost = ep.limit * ep.qty * CASH_APR * held_years
                    ep.gross, ep.fee, ep.opportunity_cost = gross, fees, cash_cost
                    ep.net = gross - fees - cash_cost
                    ep.exit_ns = ns
                    ep.state = "CLOSED_PROXY"
                self.completed.append(ep)
                self.episodes[symbol] = None
        if self.episodes[symbol] is None and book.valid_at(ns) and ns - self.last_sample_ns[symbol] >= SAMPLE_NS:
            bid = book.bid()
            if bid is None or bid <= 0:
                return
            event_id = hashlib.sha256(f"M6|{symbol}|{ns}|{bid}".encode()).hexdigest()[:24]
            self.episodes[symbol] = Episode(event_id, symbol, ns, bid, NOTIONAL / bid)
            self.last_sample_ns[symbol] = ns
            self.counts["sampled_candidate"] += 1

    def on_trade(self, symbol: str, item: dict, received_ns: int) -> None:
        if item.get("side") != "sell":
            return
        try:
            tid = int(item["trade_id"])
            price, qty = dec(item["price"]), dec(item["qty"])
            ts = parse_ns(item["timestamp"])
            if price <= 0 or qty <= 0 or ts > received_ns + 1_000_000_000 or received_ns - ts > 10_000_000_000:
                return
        except (KeyError, ValueError, InvalidOperation, TypeError, OverflowError):
            self.counts["malformed_trade"] += 1
            return
        if tid in self.seen_trade_id[symbol]:
            self.counts["duplicate_trade"] += 1
            return
        self.seen_trade_id[symbol].add(tid)
        self.counts["verified_trade_ids"] += 1
        ep = self.episodes[symbol]
        if ep is None or ep.state != "POSTED_PROXY":
            return
        # Trade-through, NOT quote-touch: side=sell, trade BELOW our resting bid.
        # This remains hypothetical because no actual order / queue ID exists.
        if self.books[symbol].valid_at(received_ns) and received_ns > ep.posted_ns and ts >= ep.posted_ns and price < ep.limit:
            ep.state = "FILLED_PROXY"
            ep.fill_ns = received_ns
            ep.exchange_fill_ns = ts
            self.counts["trade_through_proxy"] += 1

    def finish(self) -> dict:
        for symbol, ep in list(self.episodes.items()):
            if ep:
                ep.state = "CENSORED_OPEN" if ep.state == "FILLED_PROXY" else "MISSED_OR_CENSORED"
                self.completed.append(ep)
                self.episodes[symbol] = None
        by = Counter(e.state for e in self.completed)
        closed = [e for e in self.completed if e.state == "CLOSED_PROXY"]
        nets = sorted((e.net for e in closed if e.net is not None))
        net_sum = sum(nets, Decimal(0))
        median = ((nets[(len(nets)-1)//2] + nets[len(nets)//2])/2) if nets else None
        fees = sum((e.fee for e in closed if e.fee is not None), Decimal(0))
        gross = sum((e.gross for e in closed if e.gross is not None), Decimal(0))
        return {
            "schema": "NORM_03B_PUBLIC_M6_V1", "mechanism": "M6", "venue": "Kraken spot PUBLIC WS v2",
            "symbols": self.symbols, "data_source": WS_URL,
            "status": "MODEL_SCREEN_NOT_EXECUTABLE" if sum(b.received_updates for b in self.books.values()) else "DATA_BLOCKED", "eligibility": "UNKNOWN",
            "raw_data_persisted": False, "order_placement": False,
            "maker_queue": "TRADE_THROUGH_PROXY_ONLY", "fill_guarantee": False,
            "fee_scenario": {"maker": str(MAKER_FEE), "taker": str(TAKER_FEE), "apr": str(CASH_APR),
                             "source": "https://www.kraken.com/features/fee-schedule",
                             "account_tariff_verified": False},
            "nominal_usd_per_proxy": str(NOTIONAL), "linked_capital_scenario": "100 USD cash, not the NORM 10k RUB account",
            "total_candidate_episodes": len(self.completed),
            "state_counts": dict(sorted(by.items())), "closed_proxy_cycles": len(closed),
            "closed_proxy_net_sum_usd": str(net_sum),
            "closed_proxy_gross_sum_usd": str(gross), "closed_proxy_fees_usd": str(fees),
            "closed_proxy_median_net_usd": str(median) if median is not None else None,
            "positive_closed_proxy_fraction": sum(n > 0 for n in nets)/len(nets) if nets else None,
            "book_checksum_errors": sum(b.mismatch for b in self.books.values()),
            "book_valid_updates": sum(b.received_updates for b in self.books.values()),
            "unique_verified_trade_ids": sum(len(ids) for ids in self.seen_trade_id.values()),
            "counts": dict(sorted(self.counts.items())),
            "causal_receive_order_error": self.causal_error,
            "advancement": "NOT_PROVEN: no account eligibility, real maker queue/order fills, persistent raw tape or OOS multiday data",
            "reconciliation": "Missing fills and censored episodes preserved as counts; raw packet evidence not exported."
        }


async def capture(target: Path, seconds: int, symbols: list[str]) -> dict:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("MISSING_WEBSOCKETS_DEPENDENCY") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    started = time.monotonic()
    count, byte_count = 0, 0
    error = None
    try:
        async with websockets.connect(WS_URL, open_timeout=12, close_timeout=3, max_size=1_000_000,
                                      ping_interval=20, ping_timeout=15) as ws:
            for channel in ("book", "trade"):
                params = {"channel": channel, "symbol": symbols}
                if channel == "book":
                    params.update({"depth": DEPTH, "snapshot": True})
                else:
                    params["snapshot"] = False
                await ws.send(json.dumps({"method": "subscribe", "params": params}))
            with target.open("wb") as f:
                while time.monotonic() - started < seconds and count < MAX_PACKETS and byte_count < MAX_BYTES:
                    remaining = seconds - (time.monotonic() - started)
                    try:
                        payload = await asyncio.wait_for(ws.recv(), timeout=max(0.1, min(remaining, 15)))
                    except asyncio.TimeoutError:
                        if time.monotonic() - started >= seconds:
                            break
                        continue
                    if not isinstance(payload, str):
                        error = "NON_TEXT_WS_FRAME"
                        break
                    envelope = {"received_ns": utc_ns(), "payload": payload}
                    line = (json.dumps(envelope, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
                    if len(line) > 1_000_000 or byte_count + len(line) > MAX_BYTES:
                        error = "MAX_BYTES_REACHED"
                        break
                    f.write(line)
                    h.update(line)
                    byte_count += len(line)
                    count += 1
    except Exception as exc:
        # Never echo remote payloads, auth, request URL or arbitrary exception strings.
        error = "NETWORK_OR_WS_UNAVAILABLE_" + type(exc).__name__
    return {"status": "CAPTURE_COMPLETED" if count and not error else (error or "NO_WS_MESSAGES"),
            "venue": "Kraken public", "orders": 0, "packet_count": count,
            "byte_count": byte_count, "source_file_sha256": h.hexdigest(),
            "captured_utc": iso_ns(utc_ns()), "duration_ceiling_sec": seconds,
            "market_data_redistributed": False}


def replay(path: Path, source_digest: str | None = None) -> dict:
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise RuntimeError("INPUT_TOO_LARGE")
    digest = hashlib.sha256()
    screen = Screen(sorted(WHITELIST))
    packets = 0
    with path.open("rb") as f:
        for line in f:
            digest.update(line)
            packets += 1
            if packets > MAX_PACKETS:
                raise RuntimeError("INPUT_TOO_MANY_PACKETS")
            envelope = json.loads(line)
            screen.process(envelope["payload"], int(envelope["received_ns"]))
    if source_digest and digest.hexdigest() != source_digest:
        raise RuntimeError("RAW_DIGEST_MISMATCH")
    result = screen.finish()
    result.update({"raw_sha256": digest.hexdigest(), "raw_packet_count": packets, "raw_bytes": size,
                   "transient_source_data_only": True})
    return result


def cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["capture", "replay"])
    p.add_argument("--seconds", type=int, default=150)
    p.add_argument("--symbols", default="BTC/USD,ETH/USD,SOL/USD")
    p.add_argument("--path", default="03b_ephemeral_capture.jsonl")
    p.add_argument("--digest", default=None)
    p.add_argument("--out", default="03b_public_summary.json")
    args = p.parse_args()
    try:
        symbols = args.symbols.split(",")
        if not 1 <= len(symbols) <= 3 or len(symbols) != len(set(symbols)) or any(x not in WHITELIST for x in symbols):
            raise ValueError("INVALID_PUBLIC_SYMBOL_SET")
        if not 15 <= args.seconds <= MAX_SECONDS:
            raise ValueError("INVALID_DURATION")
        if args.action == "capture":
            result = asyncio.run(capture(Path(args.path), args.seconds, symbols))
            # The capture metadata may be printed. No raw payloads ever printed.
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "CAPTURE_COMPLETED" else 2
        if not Path(args.path).exists():
            raise ValueError("RAW_CAPTURE_MISSING")
        result = replay(Path(args.path), args.digest)
        # Strict summary allowlist to avoid accidental exposure of future private fields.
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "source_hash": result["raw_sha256"],
                          "valid_book_updates": result["book_valid_updates"],
                          "closed_proxy_cycles": result["closed_proxy_cycles"],
                          "advancement": result["advancement"]}, sort_keys=True))
        return 0 if not result["causal_receive_order_error"] and not result["book_checksum_errors"] else 3
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError, KeyError) as exc:
        # Deliberately report exception CLASS / stable failure code only.
        if str(exc) in ("INVALID_PUBLIC_SYMBOL_SET", "INVALID_DURATION", "RAW_CAPTURE_MISSING", "RAW_DIGEST_MISMATCH", "INPUT_TOO_LARGE", "INPUT_TOO_MANY_PACKETS"):
            code = str(exc)
        else:
            code = type(exc).__name__
        print(json.dumps({"status": "FAILED_CLOSED", "reason": code, "orders": 0}))
        return 3


if __name__ == "__main__":
    sys.exit(cli())
