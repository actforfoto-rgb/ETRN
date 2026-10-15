"""NORM ARB V7: bounded read-only Finam RTSX orderbook streaming evidence.

No orders. No account balances. No JWT or API secrets on disk or in output.
Run only against a secure read-only FINAM_READONLY_API_KEY environment variable.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from typing import Any, Callable
import urllib.error
import urllib.request

FINAM_HOST = "https://api.finam.ru"
FINAM_WS = "wss://api.finam.ru/ws"
FINAM_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9_.-]{2,40}@RTSX$")
DECIMAL_PATTERN = re.compile(r"^[+-]?[0-9]{1,18}(?:\.[0-9]{1,12})?(?:[eE][+-]?[0-9]{1,3})?$")
TIME_PATTERN = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:.+Z-]{8,28}$")
DEFAULT_SYMBOLS = (
    "CRZ6CRH7@RTSX", "SRZ6SRH7@RTSX", "GLH7GLM7@RTSX",
    "UCZ6UCH7@RTSX", "W4V6W4X6@RTSX", "W4X6W4Z6@RTSX",
)
MAX_SUBSCRIPTIONS = 6
MAX_SECONDS = 300
SCHEMA_VERSION = "NORM_FINAM_WS_EVIDENCE_V7_1"


class ControlledFailure(RuntimeError):
    """Safe failure messages that never echo credentials or broker responses."""


def validate_symbols(raw: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    symbols = tuple(x.strip().upper() for x in (raw.split(",") if isinstance(raw, str) else raw))
    if not 1 <= len(symbols) <= MAX_SUBSCRIPTIONS:
        raise ControlledFailure("SYMBOL_COUNT_OUT_OF_BOUNDS")
    if len(set(symbols)) != len(symbols) or any(not FINAM_SYMBOL_PATTERN.fullmatch(s) for s in symbols):
        raise ControlledFailure("INVALID_OR_DUPLICATE_RTSX_SYMBOL")
    return symbols


def broker_json(path: str, payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    if path not in ("/v1/sessions", "/v1/sessions/details"):
        raise ControlledFailure("NON_READONLY_AUTH_ROUTE_DENIED")
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        FINAM_HOST + path, data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise ControlledFailure(f"FINAM_AUTH_HTTP_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ControlledFailure("FINAM_AUTH_NETWORK_FAILURE") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ControlledFailure("FINAM_AUTH_INVALID_JSON") from None
    if not isinstance(result, dict):
        raise ControlledFailure("FINAM_AUTH_INVALID_JSON")
    return result


def authorize_readonly(api_secret: str) -> str:
    if not api_secret or not api_secret.startswith("tapi_"):
        raise ControlledFailure("READONLY_SECRET_NOT_CONFIGURED")
    jwt = broker_json("/v1/sessions", {"secret": api_secret}).get("token")
    if not isinstance(jwt, str) or len(jwt) < 12:
        raise ControlledFailure("FINAM_ACCESS_TOKEN_NOT_RETURNED")
    details = broker_json("/v1/sessions/details", {"token": jwt})
    if details.get("readonly") is not True:
        raise ControlledFailure("READONLY_SESSION_NOT_CONFIRMED")
    accounts = details.get("account_ids")
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise ControlledFailure("ACCOUNT_SCOPE_NOT_CONFIRMED")
    perms = details.get("md_permissions")
    if not isinstance(perms, list) or not any(
        isinstance(p, dict) and p.get("mic") == "RTSX"
        and p.get("delay_minutes") == 0
        and p.get("quote_level") is not None for p in perms
    ):
        raise ControlledFailure("RTSX_REALTIME_PERMISSION_NOT_CONFIRMED")
    return jwt


def _number_string(value: Any) -> str | None:
    """Accept only finite-shaped numeric literals, not arbitrary free text."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and DECIMAL_PATTERN.fullmatch(value):
        return value
    return None


def scrub_market_payload(payload: Any) -> dict[str, Any]:
    """Allowlist only market row fields; never serialize arbitrary broker envelopes."""
    if not isinstance(payload, dict):
        return {"unrecognized": True}
    books = payload.get("order_book")
    if books is None:
        books = payload.get("orderbook")
    if isinstance(books, dict):
        books = [books]
    if not isinstance(books, list):
        return {"unrecognized": True, "payload_fields": sorted(k for k in payload if k in ("order_book", "orderbook"))}
    clean = []
    for book in books[:MAX_SUBSCRIPTIONS]:
        if not isinstance(book, dict):
            continue
        sym = book.get("symbol")
        if not isinstance(sym, str) or not FINAM_SYMBOL_PATTERN.fullmatch(sym):
            continue
        rows = book.get("rows")
        clean_rows = []
        if isinstance(rows, list):
            for row in rows[:100]:
                if not isinstance(row, dict):
                    continue
                clean_row = {
                    key: _number_string(row.get(key))
                    for key in ("price", "buy_size", "sell_size")
                }
                action = row.get("action")
                if isinstance(action, str) and action in (
                    "ACTION_ADD", "ACTION_UPDATE", "ACTION_REMOVE", "ACTION_UNSPECIFIED"
                ):
                    clean_row["action"] = action
                timestamp = row.get("timestamp")
                if isinstance(timestamp, str) and TIME_PATTERN.fullmatch(timestamp):
                    clean_row["timestamp"] = timestamp
                clean_rows.append(clean_row)
        clean.append({"symbol": sym, "rows": clean_rows, "row_count": len(clean_rows)})
    return {"order_book": clean}


def sanitize_envelope(message: dict[str, Any], subscribed: tuple[str, ...]) -> dict[str, Any] | None:
    """Reject account/order streams and remove unknown fields, tokens and free-text errors."""
    typ = message.get("type")
    if typ == "EVENT":
        ev = message.get("event_info")
        event = ev.get("event") if isinstance(ev, dict) else None
        if event not in ("HANDSHAKE_SUCCESS", "CONNECTION_CLOSED", "SUBSCRIBED", "UNSUBSCRIBED"):
            event = "OTHER_EVENT"
        return {"kind": "EVENT", "event": event}
    if typ == "ERROR":
        err = message.get("error_info")
        code = err.get("code") if isinstance(err, dict) else None
        return {"kind": "ERROR", "error_code": code if isinstance(code, int) else None}
    if typ != "DATA" or message.get("subscription_type") != "ORDER_BOOK":
        return None
    payload = scrub_market_payload(message.get("payload"))
    if payload.get("unrecognized") or not payload.get("order_book"):
        return None
    allowed = set(subscribed)
    for book in payload.get("order_book", []):
        if book["symbol"] not in allowed:
            return None
    return {
        "kind": "DATA", "subscription_type": "ORDER_BOOK",
        "provider_timestamp": message.get("timestamp") if isinstance(message.get("timestamp"), (int, float)) else None,
        "payload": payload,
    }


async def capture_stream(
    jwt: str, symbols: tuple[str, ...], seconds: int, destination: Path,
    connector: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Short bounded WebSocket session, authenticated only after read-only gate."""
    if not isinstance(seconds, int) or not (1 <= seconds <= MAX_SECONDS):
        raise ControlledFailure("DURATION_OUT_OF_BOUNDS")
    symbols = validate_symbols(symbols)
    if connector is None:
        try:
            from websockets.asyncio.client import connect as connector  # type: ignore[no-redef]
        except ImportError:
            raise ControlledFailure("DEPENDENCY_WEBSOCKETS_MISSING") from None
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Do not permit overwriting an evidence file (creates a unique record per run).
    fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    started = datetime.now(timezone.utc).isoformat()
    subscribed = False
    data_count = 0
    error_count = 0
    event_count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as outfile:
            async with connector(FINAM_WS, ping_interval=20, ping_timeout=20, max_size=2**20, open_timeout=15) as socket:
                deadline = monotonic() + seconds
                while monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(socket.recv(), max(0.01, deadline - monotonic()))
                    except (TimeoutError, asyncio.TimeoutError):
                        break
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                        error_count += 1
                        continue
                    if not isinstance(msg, dict):
                        continue
                    entry = sanitize_envelope(msg, symbols)
                    if entry is None:
                        continue
                    entry["captured_utc"] = datetime.now(timezone.utc).isoformat()
                    entry["schema"] = SCHEMA_VERSION
                    outfile.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                    if entry["kind"] == "EVENT":
                        event_count += 1
                        if entry["event"] == "HANDSHAKE_SUCCESS" and not subscribed:
                            for symbol in symbols:
                                await socket.send(json.dumps({
                                    "action": "SUBSCRIBE", "type": "ORDER_BOOK",
                                    "data": {"symbol": symbol}, "token": jwt,
                                }, separators=(",", ":")))
                            subscribed = True
                    elif entry["kind"] == "DATA":
                        data_count += 1
                    elif entry["kind"] == "ERROR":
                        error_count += 1
    except Exception as exc:
        if isinstance(exc, ControlledFailure):
            raise
        # Never log repr(exception): external libraries may include URL headers/tokens.
        raise ControlledFailure("FINAM_WEBSOCKET_CONNECTION_FAILED") from None
    sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "schema": SCHEMA_VERSION, "started_utc": started,
        "ended_utc": datetime.now(timezone.utc).isoformat(),
        "subscribed": subscribed, "symbols_count": len(symbols),
        "market_messages": data_count, "events": event_count, "errors": error_count,
        "file": str(destination), "sha256": sha256,
        "orders": 0, "realized_pnl": 0,
        "status": "DATA_RECORDED" if data_count else ("NO_MARKET_MESSAGES" if subscribed else "NO_HANDSHAKE"),
    }


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NORM ARB V7 short read-only Finam WebSocket capture")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--output-dir", default="evidence")
    args = parser.parse_args(argv)
    try:
        symbols = validate_symbols(args.symbols)
        if not 1 <= args.seconds <= MAX_SECONDS:
            raise ControlledFailure("DURATION_OUT_OF_BOUNDS")
        api_secret = os.environ.get("FINAM_READONLY_API_KEY", "")
        jwt = authorize_readonly(api_secret)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path(args.output_dir) / ("finam_ws_" + timestamp + ".jsonl")
        summary = asyncio.run(capture_stream(jwt, symbols, args.seconds, destination))
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["market_messages"] > 0 else 2
    except ControlledFailure as exc:
        print(json.dumps({"status": str(exc), "orders": 0, "realized_pnl": 0}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(run_cli())