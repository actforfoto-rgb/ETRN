"""NORM ARBITRAGE V7 — local *read-only* Finam WS evidence audit.

Examines existing log, state and bounded local JSONL evidence files.  Does not
open network sockets, read credentials, issue broker requests or change files.
The output is allowlisted aggregate counts; no raw records are ever emitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re

MAX_FILES = 48
MAX_FILE_BYTES = 4_000_000
MAX_LOG_BYTES = 4_000_000
ALLOWED_EVENTS = {"HANDSHAKE_SUCCESS", "CONNECTION_CLOSED", "SUBSCRIBED", "UNSUBSCRIBED", "OTHER_EVENT"}
ALLOWED_STATES = {"CONNECTING", "DATA_RECEIVED", "CONNECTED_NO_UPDATES", "BROKER_STREAM_UNVERIFIED", "SUPERVISOR_ERROR", "STARTED", "STOPPED_BY_USER", "DISK_LIMIT_REACHED", "STOPPED_NO_KEY"}
ALLOWED_CYCLE_STATUSES = {"DATA_RECORDED", "SUBSCRIPTIONS_SENT_NO_MARKET_DATA", "UNVERIFIED_STREAM_ERRORS", "INCOMPLETE_LIMIT_REACHED", "NO_HANDSHAKE"}
SYMBOL = re.compile(r"^[A-Z0-9_.-]{2,40}@RTSX$")


def _time(s):
    if not isinstance(s, str) or len(s) > 40:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def _json_file_safely(path, maxbytes=MAX_LOG_BYTES):
    try:
        if path.stat().st_size > maxbytes:
            return None
        val = json.loads(path.read_text(encoding="utf-8"))
        return val if isinstance(val, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None


def audit(root: Path, max_files: int = MAX_FILES) -> dict:
    """Produce allowlisted, privacy-safe *observations*, never profit claims."""
    root = Path(root)
    state = _json_file_safely(root / "status.json") or {}
    phase = state.get("state") if state.get("state") in ALLOWED_STATES else "UNKNOWN"
    summary = {
        "schema": "NORM_ARB_G0_LOCAL_AUDIT_V1",
        "mode": "READ_ONLY_OFFLINE_NO_NETWORK",
        "actual_orders": "NOT_CHECKED_FROM_BROKER",
        "realized_profit": "NOT_ESTABLISHED",
        "current_state": phase,
        "cycles_reported": state.get("cycles") if type(state.get("cycles")) is int and 0 <= state["cycles"] < 10**8 else None,
        "files_on_disk": 0,
        "files_checked": 0,
        "files_unreadable_or_partial": 0,
        "files_oversize_or_skipped": 0,
        "handshake_events": 0,
        "subscription_ack_events": 0,
        "error_envelopes": 0,
        "market_data_envelopes": 0,
        "market_symbols": {},
        "market_rows": 0,
        "rows_buy_quantity_present": 0,
        "rows_sell_quantity_present": 0,
        "rows_both_quantities_present": 0,
        "rows_with_timestamp": 0,
        "last_data_utc": None,
        "log_completed_rounds": 0,
        "log_reported_market_messages": 0,
        "log_empty_rounds": 0,
        "log_last_cycle_status": "UNKNOWN",
        "warning": "LEVEL_UPDATES_NOT_FULL_BOOKS; NO_FILL_OR_NET_PROFIT_PROOF",
    }

    # No arbitrary raw state values ever reach output.
    data_dir = root / "data"
    try:
        files = list(data_dir.glob("finam_ws_*.jsonl"))
        files = sorted(files, key=lambda p: (p.stat().st_mtime, p.name))
    except OSError:
        files = []
    summary["files_on_disk"] = len(files)
    if not isinstance(max_files, int) or not (1 <= max_files <= MAX_FILES):
        max_files = MAX_FILES
    chosen = files[-max_files:]
    summary["files_oversize_or_skipped"] += max(0, len(files) - len(chosen))
    per_symbol = Counter()
    last_data = None

    for path in chosen:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                summary["files_oversize_or_skipped"] += 1
                continue
            summary["files_checked"] += 1
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if len(line) > 200_000:
                        summary["files_unreadable_or_partial"] += 1
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError, json.JSONDecodeError):
                        summary["files_unreadable_or_partial"] += 1
                        continue
                    if not isinstance(row, dict):
                        continue
                    kind = row.get("kind")
                    if kind == "EVENT":
                        if row.get("event") == "HANDSHAKE_SUCCESS":
                            summary["handshake_events"] += 1
                        elif row.get("event") == "SUBSCRIBED":
                            summary["subscription_ack_events"] += 1
                    elif kind == "ERROR":
                        summary["error_envelopes"] += 1
                    elif kind == "DATA" and row.get("subscription_type") == "ORDER_BOOK":
                        summary["market_data_envelopes"] += 1
                        time = _time(row.get("captured_utc"))
                        if time is not None and (last_data is None or time > last_data):
                            last_data = time
                        payload = row.get("payload")
                        books = payload.get("order_book", []) if isinstance(payload, dict) else []
                        if isinstance(books, dict):
                            books = [books]
                        if not isinstance(books, list):
                            continue
                        for book in books[:20]:
                            if not isinstance(book, dict):
                                continue
                            symbol = book.get("symbol")
                            if isinstance(symbol, str) and SYMBOL.fullmatch(symbol):
                                per_symbol[symbol] += 1
                            rows = book.get("rows", [])
                            if not isinstance(rows, list):
                                continue
                            for r in rows[:200]:
                                if not isinstance(r, dict):
                                    continue
                                summary["market_rows"] += 1
                                buy, sell = r.get("buy_size"), r.get("sell_size")
                                if isinstance(buy, str) and re.fullmatch(r"\d+(?:\.\d+)?", buy):
                                    summary["rows_buy_quantity_present"] += 1
                                if isinstance(sell, str) and re.fullmatch(r"\d+(?:\.\d+)?", sell):
                                    summary["rows_sell_quantity_present"] += 1
                                if isinstance(buy, str) and isinstance(sell, str):
                                    summary["rows_both_quantities_present"] += 1
                                if _time(r.get("timestamp")):
                                    summary["rows_with_timestamp"] += 1
        except (OSError, UnicodeError, ValueError):
            summary["files_unreadable_or_partial"] += 1
    summary["last_data_utc"] = last_data
    summary["market_symbols"] = {k:per_symbol[k] for k in sorted(per_symbol)}

    log = root / "logs" / "collector.stdout.log"
    try:
        if log.is_file():
            # Read at most a limited tail to avoid reading unbounded past history.
            with log.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - MAX_LOG_BYTES))
                chunk = fh.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
            for line in chunk.splitlines():
                if len(line) > 10_000:
                    continue
                try:
                    item = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(item, dict) or type(item.get("cycle")) is not int:
                    continue
                stat = item.get("status")
                if not isinstance(stat, str) or stat not in ALLOWED_CYCLE_STATUSES:
                    continue
                summary["log_completed_rounds"] += 1
                summary["log_last_cycle_status"] = stat
                n = item.get("market_messages")
                if type(n) is int and 0 <= n <= 10**8:
                    summary["log_reported_market_messages"] += n
                    if n == 0:
                        summary["log_empty_rounds"] += 1
    except (OSError, UnicodeError):
        pass
    if summary["market_data_envelopes"] > 0:
        summary["gate"] = "MARKET_DATA_RECORDED_NEEDS_REPLAY_AND_COST_GATE"
    elif summary["log_reported_market_messages"] > 0:
        summary["gate"] = "LOG_REPORTS_DATA_BUT_FILES_NOT_VERIFIED"
    elif summary["handshake_events"] > 0:
        summary["gate"] = "HANDSHAKE_ONLY_NO_ORDERBOOK_DATA_EVIDENCE"
    else:
        summary["gate"] = "NO_PROVEN_BROKER_MARKET_DATA"
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Safe local audit of NORM ARB Finam WS evidence. NEVER connects or trades.")
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NORM_ARB_V7_NATIVE"
    parser.add_argument("--root", default=str(default))
    parser.add_argument("--files", type=int, default=MAX_FILES)
    args = parser.parse_args(argv)
    print(json.dumps(audit(Path(args.root), args.files), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())