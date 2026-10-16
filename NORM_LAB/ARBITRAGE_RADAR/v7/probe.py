"""Bounded read-only authentication + WebSocket test, NO trades and no side effects on FX."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from tls_setup import enable_native_tls
enable_native_tls()

from finam_ws_collector import (
    authorize_readonly, capture_stream, ControlledFailure, DEFAULT_SYMBOLS
)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=25)
    args = p.parse_args(argv if argv is not None else (sys.argv[1:] if __name__ == '__main__' else []))
    key = os.getenv("FINAM_READONLY_API_KEY", "")
    if not key:
        print(json.dumps({"status": "NO_READONLY_KEY", "orders": 0}))
        return 11
    try:
        jwt = authorize_readonly(key)
        root = Path(__file__).resolve().parent / "data"
        root.mkdir(exist_ok=True)
        name = f"finam_ws_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid4().hex[:10]}.jsonl"
        result = asyncio.run(capture_stream(jwt, DEFAULT_SYMBOLS, args.seconds, root / name))
        status = ("DATA_RECEIVED" if result["market_messages"] and not result["errors"] and not result.get("limits_reached", False) else
                  "CONNECTED_NO_MARKET_UPDATES" if result["subscribed"] and not result["errors"] and not result.get("limits_reached", False) else
                  "SUBSCRIPTION_OR_CONNECTION_FAILED")
        print(json.dumps({"status": status,
                          "handshake": result["subscribed"],
                          "data_messages": result["market_messages"],
                          "errors": result["errors"],
                          "orders": 0, "realized_pnl": 0}, ensure_ascii=False))
        # A successful handshake/subscription send without any market data is NOT
        # proof of a functioning data source. Do not start a daemon on it.
        return 0 if result["market_messages"] > 0 and not result["errors"] and not result.get("limits_reached", False) else 12
    except ControlledFailure as exc:
        print(json.dumps({"status": str(exc), "orders": 0}))
        return 13
    except Exception:
        print(json.dumps({"status": "PROBE_INTERNAL_ERROR", "orders": 0}))
        return 14


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))