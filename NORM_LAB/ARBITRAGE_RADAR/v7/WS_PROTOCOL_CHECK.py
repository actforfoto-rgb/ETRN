"""NORM ARBITRAGE — one-shot read-only Finam WebSocket protocol diagnostic.

No orders, no broker account reads, no service changes, no files written, no
installation, no unverified TLS. Print allowlisted aggregate structural counts
ONLY (never raw messages, API secrets, JWT, account IDs, free-text broker errors).

Designed to run alongside an existing collector without stopping or modifying it.
Uses its vendored trusted websockets, Windows native TLS and Tk key-input UI.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from typing import Any

ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NORM_ARB_V7_NATIVE"
VEN = ROOT / "vendor"
DURATION = 90
FUTURE_SPREAD = "SRZ6SRH7@RTSX"
CONTROL_STOCK = "SBER@MISX"
FINAM_WS = "wss://api.finam.ru/ws"
FINAM_REST = "https://api.finam.ru"
ALLOWED_TYPES = ("DATA", "ERROR", "EVENT")
ALLOWED_SUB_TYPES = ("ORDER_BOOK", "QUOTES", "INSTRUMENT_TRADES", "BARS", "ORDERS", "TRADES", "ACCOUNT")
ALLOWED_EVENTS = ("HANDSHAKE_SUCCESS", "CONNECTION_CLOSED", "SUBSCRIBED", "UNSUBSCRIBED")
PAYLOAD_KINDS = ("order_book", "orderbook", "quote", "quotes", "trades", "bars", "error")
SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize_frame(msg: Any, totals: Counter, error_codes: Counter) -> bool:
    """Allowlist counters BEFORE strict production filtering. Never return raw data."""
    if not isinstance(msg, dict):
        totals["NON_OBJECT_FRAMES"] += 1
        return False
    typ = msg.get("type")
    if typ not in ALLOWED_TYPES:
        totals["UNKNOWN_TYPE_FRAMES"] += 1
        return False
    totals["FRAMES_" + typ] += 1
    if typ == "EVENT":
        ev = msg.get("event_info")
        event = ev.get("event") if isinstance(ev, dict) else None
        event = event if event in ALLOWED_EVENTS else "OTHER_EVENT"
        totals["EVENT_" + event] += 1
        return event == "HANDSHAKE_SUCCESS"
    if typ == "ERROR":
        err = msg.get("error_info")
        c = err.get("code") if isinstance(err, dict) else None
        if type(c) is int and 0 <= c <= 100000:
            error_codes[str(c)] += 1
        elif isinstance(c, str) and SAFE_CODE_RE.fullmatch(c):
            error_codes[c] += 1
        else:
            error_codes["UNKNOWN_CODE"] += 1
        return False
    kind = msg.get("subscription_type")
    kind = kind if kind in ALLOWED_SUB_TYPES else "UNKNOWN"
    totals["DATA_SUBSCRIPTION_" + kind] += 1
    payload = msg.get("payload")
    if not isinstance(payload, dict):
        totals["DATA_PAYLOAD_NOT_OBJECT"] += 1
        return False
    found = False
    for k in PAYLOAD_KINDS:
        if k in payload:
            found = True
            value = payload.get(k)
            totals["PAYLOAD_" + k.upper()] += 1
            if isinstance(value, (dict, list)):
                totals["PAYLOAD_" + k.upper() + "_NONEMPTY"] += int(bool(value))
    if not found:
        totals["DATA_PAYLOAD_UNKNOWN_SHAPE"] += 1
    return False


def rest_public_structure(jwt: str, symbol: str, route: str) -> dict[str, Any]:
    """One authenticated read-only GET; returned metrics never contain broker raw data."""
    assert route in ("orderbook", "quotes/latest")
    address = FINAM_REST + "/v1/instruments/" + quote(symbol, safe="@") + "/" + route
    request = Request(address, headers={"Authorization": "Bearer " + jwt, "Accept": "application/json"}, method="GET")
    result = {"status": "NOT_CHECKED", "has_rows": False, "has_bid_and_ask": False}
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read(600000))
        result["status"] = "HTTP_200"
        if isinstance(payload, dict):
            body = payload.get("orderbook") if route == "orderbook" else payload
            rows = body.get("rows") if isinstance(body, dict) else None
            result["has_rows"] = bool(isinstance(rows, list) and rows)
            if isinstance(rows, list):
                # Availability ≠ freshness/fill: count only strictly positive sizes,
                # no broker prices, volumes or timestamps ever leave this process.
                def positive(row: dict, side: str) -> bool:
                    val = row.get(side)
                    if isinstance(val, dict):
                        val = val.get("value")
                    if not isinstance(val, (str, int, float)) or isinstance(val, bool):
                        return False
                    try:
                        num = Decimal(str(val))
                        return num.is_finite() and num > 0
                    except (InvalidOperation, ValueError):
                        return False
                result["has_bid_and_ask"] = (any(isinstance(r, dict) and positive(r, "buy_size") for r in rows) and
                                             any(isinstance(r, dict) and positive(r, "sell_size") for r in rows))
            if route != "orderbook":
                result["quote_object_present"] = "quote" in payload or "symbol" in payload
    except HTTPError as exc:
        result["status"] = "HTTP_" + str(exc.code)
    except (URLError, TimeoutError, OSError):
        result["status"] = "NETWORK_OR_TLS_FAILURE"
    except (UnicodeError, json.JSONDecodeError):
        result["status"] = "INVALID_JSON"
    return result


async def observe(jwt: str, seconds: int = DURATION, connector=None) -> dict[str, Any]:
    """One bounded independent socket with two documented market subscriptions."""
    if not 20 <= seconds <= 120:
        raise ValueError("DURATION_OUT_OF_BOUNDS")
    if connector is None:
        from websockets.asyncio.client import connect
        connector = connect
    totals = Counter()
    error_codes = Counter()
    sent = False
    stop = "TIME_LIMIT"
    started = utc()
    try:
        async with connector(FINAM_WS, open_timeout=15, ping_interval=20, ping_timeout=20,
                             max_size=1024*1024, max_queue=16) as socket:
            deadline = monotonic() + seconds
            while monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=max(0.02, deadline-monotonic()))
                except asyncio.TimeoutError:
                    break
                except Exception:
                    stop = "STREAM_CLOSED_OR_RECV_FAILED"
                    break
                if not isinstance(raw, (str, bytes)) or len(raw) > 1024 * 1024:
                    totals["UNSUPPORTED_RAW_FRAME"] += 1
                    continue
                totals["RECEIVED_FRAMES_TOTAL"] += 1
                try:
                    obj = json.loads(raw)
                except (ValueError, TypeError, UnicodeError):
                    totals["INVALID_JSON_FRAMES"] += 1
                    continue
                handshake = summarize_frame(obj, totals, error_codes)
                if handshake and not sent:
                    # Only public market data; never ORDERS, TRADES (account) or ACCOUNT.
                    # The two request shapes are documented by Finam AsyncAPI / getting-started.
                    for typ, data in (
                        ("ORDER_BOOK", {"symbol": FUTURE_SPREAD}),
                        ("QUOTES", {"symbols": [CONTROL_STOCK]}),
                    ):
                        try:
                            await socket.send(json.dumps({"action": "SUBSCRIBE", "type": typ,
                                                          "data": data, "token": jwt}, separators=(",", ":")))
                        except Exception:
                            stop = "SUBSCRIBE_SEND_FAILED"
                            break
                        totals["SENT_SUBSCRIBE_" + typ] += 1
                    sent = True
                    if stop == "SUBSCRIBE_SEND_FAILED":
                        break
                if totals.get("FRAMES_ERROR", 0) > 5:
                    stop = "TOO_MANY_ERROR_FRAMES"
                    break
    except Exception:
        stop = "CONNECT_OR_TLS_FAILED"
    report = {
        "schema": "NORM_ARB_FINAM_WS_WIRE_AUDIT_V1",
        "mode": "READ_ONLY_NO_TRADES_NO_DISK_WRITES",
        "started_utc": started,
        "ended_utc": utc(),
        "duration_limit_seconds": seconds,
        "subscriptions": ["ONE_RTSX_ORDER_BOOK", "ONE_MISX_QUOTE_CONTROL"],
        "stop_reason": stop,
        "counters": {k: totals[k] for k in sorted(totals)},
        "error_codes_only": {k: error_codes[k] for k in sorted(error_codes)},
        "conclusion": "DATA_FRAMES_SEEN_CHECK_PAYLOAD_SHAPE" if totals.get("FRAMES_DATA") else
                      "ONLY_WS_HANDSHAKE_OR_ERRORS_NO_DATA",
        "broker_orders_sent": 0,
        "realized_profit": "NOT_ESTABLISHED",
    }
    return report


def main() -> int:
    if os.name != "nt":
        print(json.dumps({"status": "WINDOWS_VPS_ONLY", "orders": 0}))
        return 2
    if not (ROOT / "START.py").is_file() or not (VEN / "websockets" / "__init__.py").is_file():
        print(json.dumps({"status": "EXISTING_COLLECTOR_NOT_FOUND", "orders": 0}))
        return 2
    sys.dont_write_bytecode = True  # no __pycache__ writes into the running collector directory
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(VEN))
    # Use the existing Windows native trust store. No unverified TLS contexts.
    from tls_setup import enable_native_tls
    enable_native_tls()
    from finam_ws_collector import authorize_readonly, ControlledFailure
    from START import get_key_from_dialog

    key = get_key_from_dialog()
    if key is None:
        print(json.dumps({"status": "KEY_INPUT_CANCELLED", "orders": 0}))
        return 2
    try:
        token = authorize_readonly(key)  # requires readonly=true and realtime RTSX permission
        key = ""  # no key persisted or logged
        summary = {
            "read_only_auth": "VERIFIED",
            "rest_rtsx_orderbook": rest_public_structure(token, FUTURE_SPREAD, "orderbook"),
            "rest_misx_quote_control": rest_public_structure(token, CONTROL_STOCK, "quotes/latest"),
            "websocket": asyncio.run(observe(token)),
        }
        # Only allowlisted summaries, without broker free-text responses.
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if summary["websocket"]["counters"].get("FRAMES_DATA", 0) else 3
    except ControlledFailure as exc:
        print(json.dumps({"status": str(exc), "orders": 0}, ensure_ascii=False))
        return 3
    except Exception:
        print(json.dumps({"status": "PROBE_INTERNAL_FAILURE", "orders": 0}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())