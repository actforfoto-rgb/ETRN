# -*- coding: utf-8 -*-
"""NORM ARB V7: one-shot read-only Finam WS payload-shape recovery.

For an existing Windows NORM_ARB_V7_NATIVE install. No orders, services,
PowerShell, package installation, or changes to the running collector.

Count real incoming envelope and payload types, including missing/list/string
payloads. Only write allowlisted market quote / order-book fields locally, and
never write raw messages, broker account data, secret keys, or JWT tokens.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any

ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NORM_ARB_V7_NATIVE"
VENDOR = ROOT / "vendor"
OUTPUT = ROOT.parent / "NORM_ARB_WS_RECOVERY_V2"
WS = "wss://api.finam.ru/ws"
BOOK_SYMBOL = "SRZ6SRH7@RTSX"
QUOTE_SYMBOL = "SBER@MISX"
DURATION_SECONDS = 40
MAX_WIRE_BYTES = 1024 * 1024
MAX_FRAMES = 2000
MAX_MARKET_RECORDS = 250
MAX_MARKET_BYTES = 1_000_000
MAX_NESTED_STRING = 200_000
SYM = re.compile(r"^[A-Z0-9_.-]{1,40}@[A-Z0-9]{4}$")
NUM = re.compile(r"^[+-]?(?:[0-9]{1,18})(?:\.[0-9]{1,12})?(?:[eE][+-]?[0-9]{1,3})?$")
ALLOWED_ACTIONS = {"ACTION_UNSPECIFIED", "ACTION_REMOVE", "ACTION_ADD", "ACTION_UPDATE"}
SAFE_DATA_NAMES = ("orderBook", "order_book", "orderbook", "quote", "quotes", "rows")


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def shape(value: Any, exists: bool = True) -> str:
    if not exists:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "other"


def safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not 15 <= len(value) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def as_decimal(value: Any, *, qty: bool = False) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    raw = str(value)
    if not NUM.fullmatch(raw):
        return None
    try:
        v = Decimal(raw)
    except InvalidOperation:
        return None
    if not v.is_finite() or (qty and v < 0):
        return None
    return str(v)


def normalize_blob(value: Any, kind: str, count: Counter) -> dict | list | None:
    """Normalize only container encoding; never invent market fields."""
    k = shape(value)
    count["payload_type_" + kind + "_" + k] += 1
    if isinstance(value, str):
        if len(value) > MAX_NESTED_STRING:
            count["payload_string_too_large"] += 1
            return None
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, UnicodeError):
            count["payload_string_not_json"] += 1
            return None
        count["payload_string_json_to_" + shape(value)] += 1
    return value if isinstance(value, (dict, list)) else None


def find_payload(frame: dict, count: Counter) -> Any:
    # Documented 'payload' takes precedence. 'data' is only investigated when
    # the documented field is absent/invalid; this never exposes raw values.
    if "payload" in frame:
        value = normalize_blob(frame.get("payload"), "payload", count)
        if value is not None:
            count["location_payload"] += 1
            return value
    else:
        count["payload_field_missing"] += 1
    if "data" in frame:
        value = normalize_blob(frame.get("data"), "data", count)
        if value is not None:
            count["location_data"] += 1
            return value
    else:
        count["data_field_missing"] += 1
    for name in ("orderBook", "order_book", "orderbook", "quote", "quotes"):
        if name in frame:
            val = normalize_blob(frame.get(name), "top_" + name, count)
            if val is not None:
                count["location_top_" + name] += 1
                return {name: val}
    return None


def select_market(blob: Any, kind: str, count: Counter) -> list[dict]:
    """Extract only venue market arrays; never account/order streams."""
    if kind == "ORDER_BOOK":
        names = ("orderBook", "order_book", "orderbook")
    elif kind == "QUOTES":
        names = ("quote", "quotes")
    else:
        return []
    if isinstance(blob, dict):
        for key in names:
            if key in blob:
                value = normalize_blob(blob[key], "inner_" + key, count)
                if isinstance(value, list):
                    return [x for x in value[:100] if isinstance(x, dict)]
                if isinstance(value, dict):
                    return [value]
        # Allow a directly represented single market object, but only if it
        # declares exactly the known instrument and enough market-only fields.
        if kind == "ORDER_BOOK" and blob.get("symbol") == BOOK_SYMBOL and isinstance(blob.get("rows"), list):
            count["direct_book_object"] += 1
            return [blob]
        if kind == "QUOTES" and blob.get("symbol") == QUOTE_SYMBOL and any(x in blob for x in ("bid", "ask", "last")):
            count["direct_quote_object"] += 1
            return [blob]
        count["object_unknown_market_keys"] += 1
        for name in SAFE_DATA_NAMES:
            if name in blob:
                count["recognized_key_" + name] += 1
        return []
    if isinstance(blob, list):
        count["direct_payload_list"] += 1
        return [x for x in blob[:100] if isinstance(x, dict)]
    return []


def _proto_field(row: dict, snake: str, camel: str, count: Counter) -> tuple[Any, bool]:
    """ProtoJSON default uses lowerCamelCase; accept original proto names too.

    Ambiguous duplicate names with different values must not silently select a
    side or size. Real values are NEVER written to counters or error messages.
    """
    old = snake in row
    new = camel in row
    if old and new and row[snake] != row[camel]:
        count["conflicting_protojson_alias"] += 1
        return None, False
    if new:
        count["protojson_camel_field_" + camel] += 1
        return row[camel], True
    if old:
        return row[snake], True
    return None, False


def sanitize_orderbooks(entries: list[dict], count: Counter) -> list[dict]:
    out = []
    for item in entries:
        sym = item.get("symbol")
        if not isinstance(sym, str) or not SYM.fullmatch(sym) or sym != BOOK_SYMBOL:
            count["book_wrong_or_missing_symbol"] += 1
            continue
        rows = item.get("rows")
        if not isinstance(rows, list):
            count["book_rows_not_list"] += 1
            continue
        count["book_messages_with_rows_array"] += 1
        safe_rows = []
        for row in rows[:150]:
            if not isinstance(row, dict):
                count["book_non_object_row"] += 1
                continue
            price = as_decimal(row.get("price"))
            if price is None:
                count["book_invalid_price"] += 1
                continue
            action = row.get("action")
            if action is not None and action not in ALLOWED_ACTIONS:
                count["book_invalid_action"] += 1
                continue
            braw, bfound = _proto_field(row, "buy_size", "buySize", count)
            sraw, sfound = _proto_field(row, "sell_size", "sellSize", count)
            buy = as_decimal(braw, qty=True) if bfound else None
            sell = as_decimal(sraw, qty=True) if sfound else None
            # Proto defines oneof side. For a live WS delta, a simultaneous
            # buySize+sellSize can only be accepted after separate provenance
            # verification, not assumed a valid side-specific update.
            if bfound and sfound:
                count["book_row_two_sides_ambiguous"] += 1
                continue
            # Finam order book row protobuf uses ONEOF buy/sell. Omitted other
            # side cannot be treated as an explicit zero.
            if action != "ACTION_REMOVE" and buy is None and sell is None:
                count["book_row_missing_sides"] += 1
                continue
            sr = {"price": price}
            if buy is not None:
                sr["buy_size"] = buy
            if sell is not None:
                sr["sell_size"] = sell
            if action is not None:
                sr["action"] = action
            stamp = row.get("timestamp")
            t = safe_timestamp(stamp)
            if t is not None:
                sr["timestamp"] = t
            safe_rows.append(sr)
            count["book_rows_sanitized"] += 1
        if safe_rows:
            ev = {"symbol": sym, "rows": safe_rows}
            snapshot, snapfound = _proto_field(item, "is_data_snapshot", "isDataSnapshot", count)
            if snapfound and isinstance(snapshot, bool):
                ev["is_data_snapshot"] = snapshot
            out.append(ev)
            count["book_messages_sanitized"] += 1
    return out


def sanitize_quotes(entries: list[dict], count: Counter) -> list[dict]:
    out = []
    for item in entries:
        sym = item.get("symbol")
        if sym != QUOTE_SYMBOL:
            count["quote_wrong_or_missing_symbol"] += 1
            continue
        q = {"symbol": sym}
        for key in ("bid", "ask", "last", "bid_size", "ask_size"):
            num = as_decimal(item.get(key), qty=key.endswith("size"))
            if num is not None:
                q[key] = num
        stamp = item.get("timestamp")
        t = safe_timestamp(stamp)
        if t is not None:
            q["timestamp"] = t
        if len(q) > 1:
            out.append(q)
            count["quotes_sanitized"] += 1
    return out


def interpret_data(frame: Any, stats: Counter) -> dict | None:
    if not isinstance(frame, dict) or frame.get("type") != "DATA":
        return None
    stats["frames_data"] += 1
    kind = frame.get("subscription_type")
    if kind not in ("ORDER_BOOK", "QUOTES"):
        stats["ignored_non_market_subscription"] += 1
        return None
    stats["frames_" + kind.lower()] += 1
    blob = find_payload(frame, stats)
    if blob is None:
        stats["data_without_recognized_container"] += 1
        return None
    entries = select_market(blob, kind, stats)
    if not entries:
        stats["data_no_market_entries"] += 1
        return None
    items = sanitize_orderbooks(entries, stats) if kind == "ORDER_BOOK" else sanitize_quotes(entries, stats)
    if not items:
        stats["data_no_valid_price_or_rows"] += 1
        return None
    pstamp = frame.get("timestamp")
    evidence = {"kind": kind, "captured_utc": utc(), "items": items, "schema": "NORM_ARB_SAFE_WS_EVENT_V2"}
    if type(pstamp) is int and 0 <= pstamp <= 3000000000:
        evidence["provider_timestamp"] = pstamp
    return evidence


async def observe(jwt: str, seconds: int = DURATION_SECONDS, connector=None) -> tuple[dict, list[dict]]:
    if not 15 <= seconds <= 120:
        raise ValueError("DURATION_BOUNDS")
    if connector is None:
        from websockets.asyncio.client import connect
        connector = connect
    counts: Counter = Counter()
    received = []
    byte_budget = MAX_MARKET_BYTES
    saw_handshake = False
    started_at = utc()
    stop = "TIME_LIMIT"
    try:
        async with connector(WS, open_timeout=12, ping_interval=20, ping_timeout=20,
                             max_queue=16, max_size=MAX_WIRE_BYTES) as socket:
            deadline = monotonic() + seconds
            while monotonic() < deadline and counts["frames_total"] < MAX_FRAMES:
                try:
                    frame_raw = await asyncio.wait_for(socket.recv(), max(0.03, deadline - monotonic()))
                except (TimeoutError, asyncio.TimeoutError):
                    break
                except Exception:
                    stop = "STREAM_CLOSED_OR_RECV_FAILED"
                    break
                counts["frames_total"] += 1
                if not isinstance(frame_raw, (str, bytes)) or len(frame_raw) > MAX_WIRE_BYTES:
                    counts["frames_invalid_size_or_type"] += 1
                    continue
                try:
                    msg = json.loads(frame_raw)
                except (ValueError, TypeError, UnicodeError):
                    counts["frames_invalid_json"] += 1
                    continue
                if not isinstance(msg, dict):
                    counts["frames_json_non_object"] += 1
                    continue
                typ = msg.get("type")
                if typ == "EVENT":
                    counts["frames_event"] += 1
                    ev = msg.get("event_info")
                    event = ev.get("event") if isinstance(ev, dict) else None
                    if event == "HANDSHAKE_SUCCESS" and not saw_handshake:
                        saw_handshake = True
                        counts["handshake_success"] += 1
                        for typ, data in (("ORDER_BOOK", {"symbol": BOOK_SYMBOL}),
                                          ("QUOTES", {"symbols": [QUOTE_SYMBOL]})):
                            await socket.send(json.dumps({"action": "SUBSCRIBE", "type": typ,
                                                          "data": data, "token": jwt}, separators=(",", ":")))
                            counts["subscriptions_sent_" + typ.lower()] += 1
                    elif event == "CONNECTION_CLOSED":
                        stop = "SERVER_CONNECTION_CLOSED"
                        break
                    continue
                if typ == "ERROR":
                    counts["frames_error"] += 1
                    continue  # no free-form text from broker
                event = interpret_data(msg, counts)
                if event is not None:
                    event_bytes = len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
                    if (len(received) < MAX_MARKET_RECORDS and event_bytes <= byte_budget):
                        received.append(event)
                        byte_budget -= event_bytes
                    else:
                        counts["sanitized_capture_limit_reached"] += 1
                elif typ != "DATA":
                    counts["frames_unknown_type"] += 1
                if counts["frames_error"] > 5:
                    stop = "BROKER_ERRORS"
                    break
    except Exception:
        stop = "CONNECT_OR_TLS_ERROR"
    if counts["frames_total"] >= MAX_FRAMES:
        stop = "FRAMES_LIMIT"
    has_book = counts["book_rows_sanitized"] > 0
    has_quotes = counts["quotes_sanitized"] > 0
    if has_book:
        conclusion = "BOOK_ROWS_CAPTURED_SANITIZED_NO_FILL_PROOF"
    elif has_quotes:
        conclusion = "QUOTES_CAPTURED_BOOK_UNVERIFIED"
    elif counts["frames_data"]:
        conclusion = "DATA_SEEN_PAYLOAD_SHAPE_UNKNOWN"
    else:
        conclusion = "NO_DATA_FRAMES"
    return ({"schema": "NORM_ARB_WS_SHAPE_AND_CAPTURE_V2",
             "started_utc": started_at, "ended_utc": utc(), "duration_seconds": seconds, "stop_reason": stop,
             "conclusion": conclusion, "counters": dict(sorted(counts.items())),
             "sanitized_events_buffered": len(received),
             "orders_sent": 0, "profit_proven": False}, received)


def save_only_sanitized(report: dict, records: list[dict]) -> None:
    """Only market-only allowlisted evidence is persisted. Existing service is untouched."""
    if not records:
        return
    OUTPUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = "ws_market_only_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl"
    path = OUTPUT / name
    digest = hashlib.sha256()
    with path.open("x", encoding="utf-8", newline="\n") as f:
        for ev in records:
            row = json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n"
            f.write(row)
            digest.update(row.encode("utf-8"))
    report["evidence_file"] = str(path)
    report["evidence_sha256"] = digest.hexdigest()
    report["evidence_warning"] = "MARKET_ONLY_NOT_VERIFIED_EXECUTABLE_BOOK"


def main() -> int:
    if os.name != "nt":
        print(json.dumps({"status": "WINDOWS_VPS_ONLY"}))
        return 2
    if not (ROOT / "START.py").is_file() or not (VENDOR / "websockets" / "__init__.py").is_file():
        print(json.dumps({"status": "EXISTING_COLLECTOR_MISSING"}))
        return 2
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(VENDOR))
    from tls_setup import enable_native_tls
    enable_native_tls()
    from finam_ws_collector import authorize_readonly, ControlledFailure
    from START import get_key_from_dialog
    secret = get_key_from_dialog()
    if secret is None:
        print(json.dumps({"status": "CANCELLED"}))
        return 2
    try:
        jwt = authorize_readonly(secret)
        secret = ""
        report, records = asyncio.run(observe(jwt))
        jwt = ""
        save_only_sanitized(report, records)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["counters"].get("book_rows_sanitized", 0) else 3
    except ControlledFailure as exc:
        print(json.dumps({"status": str(exc), "orders_sent": 0}))
        return 3
    except Exception:
        print(json.dumps({"status": "RECOVERY_PROBE_INTERNAL_FAILURE", "orders_sent": 0}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())