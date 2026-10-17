"""NORM ARB V7: bounded read-only Finam RTSX orderbook streaming evidence.

No orders. No account balances. No JWT or API secrets on disk or in output.
Run only against a secure read-only FINAM_READONLY_API_KEY environment variable.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
import ssl
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
# Relevant market PRINTS to model maker fills; not account trades.
DEFAULT_TRADE_SYMBOLS = ("W4V6W4X6@RTSX", "W4X6W4Z6@RTSX")
MAX_SECONDS = 300
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_DATA_MESSAGES = 10000
# Legacy test injection points; enforce these too as stricter data caps.
MAX_EVIDENCE_BYTES = MAX_FILE_BYTES
MAX_EVIDENCE_RECORDS = MAX_DATA_MESSAGES
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


def select_trade_symbols(book_symbols: tuple[str, ...], raw: str | None = None) -> tuple[str, ...]:
    """Default to available WHEAT trade subscriptions; opt in explicitly for other RTSX symbols.

    An empty --trade-symbols disables print capture; every trade subscription
    MUST also be in the user-selected book universe.
    """
    if raw is None:
        return tuple(symbol for symbol in DEFAULT_TRADE_SYMBOLS if symbol in book_symbols)
    if not isinstance(raw, str):
        raise ControlledFailure('INVALID_TRADE_SYMBOL_ARGUMENT')
    if not raw.strip():
        return ()
    selected = validate_symbols(raw)
    if not set(selected).issubset(set(book_symbols)):
        raise ControlledFailure('TRADE_SYMBOLS_MUST_HAVE_ORDER_BOOK_SUBSCRIPTION')
    return selected


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
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise ControlledFailure("FINAM_TLS_CERTIFICATE_UNTRUSTED") from None
        raise ControlledFailure("FINAM_AUTH_NETWORK_FAILURE") from None
    except ssl.SSLCertVerificationError:
        raise ControlledFailure("FINAM_TLS_CERTIFICATE_UNTRUSTED") from None
    except (TimeoutError, OSError):
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
    """Parse Finam ProtoJSON and keep only explicit market-side fields.

    WebSocket payload is a JSON STRING containing proto-json, and proto's
    order_book/buy_size/sell_size/is_data_snapshot are encoded by default as
    orderBook/buySize/sellSize/isDataSnapshot. Ignore account fields and JWTs.
    """
    if isinstance(payload, str):
        if len(payload) > 200_000:
            return {"unrecognized": True}
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return {"unrecognized": True}
    if not isinstance(payload, dict):
        return {"unrecognized": True}

    aliases = ("orderBook", "order_book", "orderbook")
    keys = [key for key in aliases if key in payload]
    if len(keys) != 1:
        return {"unrecognized": True}
    books = payload[keys[0]]
    if isinstance(books, dict):
        books = [books]
    if not isinstance(books, list) or not books or len(books) > MAX_SUBSCRIPTIONS:
        return {"unrecognized": True}
    clean: list[dict[str, Any]] = []
    for book in books:
        if not isinstance(book, dict):
            return {"unrecognized": True}
        sym = book.get("symbol")
        if not isinstance(sym, str) or not FINAM_SYMBOL_PATTERN.fullmatch(sym):
            return {"unrecognized": True}
        rows = book.get("rows")
        if not isinstance(rows, list) or not rows or len(rows) > 200:
            return {"unrecognized": True}
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                return {"unrecognized": True}
            price = _number_string(row.get("price"))
            if price is None:
                return {"unrecognized": True}
            # Refuse conflicting duplicate protobuf and ProtoJSON alias values.
            if (("buySize" in row and "buy_size" in row and row["buySize"] != row["buy_size"]) or
                    ("sellSize" in row and "sell_size" in row and row["sellSize"] != row["sell_size"])):
                return {"unrecognized": True}
            buy_value = row["buySize"] if "buySize" in row else row.get("buy_size")
            sell_value = row["sellSize"] if "sellSize" in row else row.get("sell_size")
            buy = _number_string(buy_value)
            sell = _number_string(sell_value)
            action = row.get("action")
            if action is not None and action not in (
                "ACTION_ADD", "ACTION_UPDATE", "ACTION_REMOVE", "ACTION_UNSPECIFIED"
            ):
                return {"unrecognized": True}
            if buy is None and sell is None and action != "ACTION_REMOVE":
                return {"unrecognized": True}
            rr: dict[str, Any] = {"price": price}
            if buy is not None:
                rr["buy_size"] = buy
            if sell is not None:
                rr["sell_size"] = sell
            if action is not None:
                rr["action"] = action
            timestamp = row.get("timestamp")
            if isinstance(timestamp, str) and TIME_PATTERN.fullmatch(timestamp):
                rr["timestamp"] = timestamp
            normalized_rows.append(rr)
        if not normalized_rows:
            return {"unrecognized": True}
        result_book: dict[str, Any] = {"symbol": sym, "rows": normalized_rows,
                                        "row_count": len(normalized_rows)}
        if "isDataSnapshot" in book and "is_data_snapshot" in book and (book["isDataSnapshot"] != book["is_data_snapshot"]):
            return {"unrecognized": True}
        flag = book["isDataSnapshot"] if "isDataSnapshot" in book else book.get("is_data_snapshot")
        if isinstance(flag, bool):
            result_book["is_data_snapshot"] = flag
        clean.append(result_book)
    return {"order_book": clean} if clean else {"unrecognized": True}


def scrub_market_trades(payload: Any, allowed: tuple[str, ...]) -> dict[str, Any]:
    """Never mistake public instrument trades for private account TRADES.

    Finam SubscribeLatestTradesResponse: symbol, repeated trades. ProtoJSON
    may camel-case tradeId and decimal values. Preserve only market prices,
    positive sizes and timestamps, with SHA256-shortened public trade id.
    """
    if isinstance(payload, str):
        if len(payload)>200_000:
            return {'unrecognized':True}
        try:
            payload=json.loads(payload)
        except (ValueError,TypeError):
            return {'unrecognized':True}
    if not isinstance(payload,dict):
        return {'unrecognized':True}
    sym=payload.get('symbol')
    trades=payload.get('trades')
    if not isinstance(sym,str) or sym not in allowed or not isinstance(trades,list) or len(trades)>200:
        return {'unrecognized':True}
    items=[]
    for item in trades:
        if not isinstance(item,dict):
            continue
        price=_number_string(item.get('price'))
        size=_number_string(item.get('size'))
        stamp=item.get('timestamp')
        if price is None or size is None or not isinstance(stamp,str) or not TIME_PATTERN.fullmatch(stamp):
            continue
        try:
            if Decimal(size)<=0:
                continue
        except (InvalidOperation,ValueError):
            continue
        value={'price':price,'size':size,'timestamp':stamp}
        # The OFFICIAL Finam marketdata Trade proto has a PER-TRADE
        # is_data_snapshot bool (field 8). Do not drop it: historic/batch
        # trades must not satisfy a newly posted hypothetical order.
        if ('isDataSnapshot' in item and 'is_data_snapshot' in item
                and item['isDataSnapshot'] != item['is_data_snapshot']):
            continue
        raw_snapshot = (item['isDataSnapshot'] if 'isDataSnapshot' in item
                        else item.get('is_data_snapshot', False))
        if type(raw_snapshot) is not bool:
            continue
        # Absence represents proto3's FALSE default for trusted ProtoJSON.
        value['is_data_snapshot'] = raw_snapshot
        side=item.get('side')
        if side in ('SIDE_BUY','SIDE_SELL','BUY','SELL'):
            value['side']=side
        tid=item.get('tradeId',item.get('trade_id'))
        if isinstance(tid,str) and 1<=len(tid)<=100:
            value['public_trade_id_hash']=hashlib.sha256(tid.encode('utf-8')).hexdigest()[:24]
        items.append(value)
    return {'symbol':sym,'trades':items} if items else {'unrecognized':True}


def sanitize_envelope(message: dict[str, Any], subscribed: tuple[str, ...],
                      trade_symbols: tuple[str,...] = ()) -> dict[str, Any] | None:
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
    if typ != 'DATA':
        return None
    if message.get('subscription_type')=='INSTRUMENT_TRADES':
        if not trade_symbols:
            return None
        market=scrub_market_trades(message.get('payload'),trade_symbols)
        if market.get('unrecognized'):
            return None
        return {'kind':'TRADE_PRINTS','subscription_type':'INSTRUMENT_TRADES',
            'provider_timestamp':message.get('timestamp') if type(message.get('timestamp')) is int and message.get('timestamp')>=0 else None,
            'payload':market}
    if message.get('subscription_type') != 'ORDER_BOOK':
        return None
    payload = scrub_market_payload(message.get('payload'))
    if payload.get("unrecognized") or not payload.get("order_book"):
        return None
    allowed = set(subscribed)
    for book in payload.get("order_book", []):
        if book["symbol"] not in allowed:
            return None
    return {
        "kind": "DATA", "subscription_type": "ORDER_BOOK",
        "provider_timestamp": message.get("timestamp") if type(message.get("timestamp")) is int and message.get("timestamp") >= 0 else None,
        "payload": payload,
    }


async def capture_stream(
    jwt: str, symbols: tuple[str, ...], seconds: int, destination: Path,
    connector: Callable[..., Any] | None = None,
    trade_symbols: tuple[str,...] = (),
) -> dict[str, Any]:
    """Bounded Finam read-only stream, allowlisted tape, independent book QA.

    The only emitted book data are sanitized market-only fields. Missing valid
    WS snapshots, dropped rows, stale level times and no sequence numbers make
    the book NON-EXECUTABLE by construction. An in-memory reconstructor reads
    the same allowlisted events, not raw authenticated envelopes.
    """
    from book_replay import BookReplay
    if not isinstance(seconds,int) or not 1 <= seconds <= MAX_SECONDS:
        raise ControlledFailure('DURATION_OUT_OF_BOUNDS')
    symbols=validate_symbols(symbols)
    if not isinstance(trade_symbols,tuple) or len(set(trade_symbols))!=len(trade_symbols) or any(s not in symbols for s in trade_symbols):
        raise ControlledFailure('INVALID_MARKET_TRADE_UNIVERSE')
    if not isinstance(jwt,str) or not jwt:
        raise ControlledFailure('ACCESS_TOKEN_MISSING')
    if connector is None:
        try:
            from websockets.asyncio.client import connect as connector
        except ImportError:
            raise ControlledFailure('DEPENDENCY_WEBSOCKETS_MISSING') from None
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(str(destination),os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
    started=datetime.now(timezone.utc).isoformat()
    sent=False
    ndata=errors=events=records=byte_count=unknown=unsolicited=0
    ntrades=trade_prints=0
    truncated=False
    snapshots=0
    book_rows=0
    last_data_utc=None
    stop_reason='TIME_LIMIT'
    digest=hashlib.sha256()
    replay=BookReplay(symbols)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',buffering=1) as out:
            async with connector(FINAM_WS,ping_interval=20,ping_timeout=20,
                                 max_size=1024*1024,open_timeout=15) as ws:
                deadline=monotonic()+seconds
                while monotonic()<deadline:
                    try:
                        frame=await asyncio.wait_for(ws.recv(),max(.01,deadline-monotonic()))
                    except (TimeoutError,asyncio.TimeoutError):
                        break
                    except Exception:
                        errors+=1
                        stop_reason='WS_READ_DISCONNECTED'
                        break
                    if not isinstance(frame,(str,bytes)) or len(frame)>1024*1024:
                        errors+=1
                        continue
                    try:
                        msg=json.loads(frame)
                    except (ValueError,TypeError,UnicodeError):
                        errors+=1
                        continue
                    if not isinstance(msg,dict):
                        errors+=1
                        continue
                    entry=sanitize_envelope(msg,symbols,trade_symbols)
                    if entry is None:
                        if msg.get('type')=='DATA' and msg.get('subscription_type') in ('ORDER_BOOK','INSTRUMENT_TRADES'):
                            unknown+=1
                            if msg.get('subscription_type')=='ORDER_BOOK':
                                # A missing/malformed delta makes all books untrusted.
                                # Never silently skip it and retain fake continuity.
                                replay.invalidate_all('UNPARSEABLE_WS_FRAME')
                        continue
                    if entry['kind'] in ('DATA','TRADE_PRINTS') and not sent:
                        unsolicited+=1
                        errors+=1  # Data arrived without a confirmed subscription.
                        continue
                    # Read-only evidence is tightly allowlisted; never pass
                    # raw frames, error free text, JWT or account data here.
                    received=datetime.now(timezone.utc)
                    entry['captured_utc']=received.isoformat()
                    entry['schema']=SCHEMA_VERSION
                    line=json.dumps(entry,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n'
                    raw_line=line.encode('utf-8')
                    if records>=min(MAX_DATA_MESSAGES,MAX_EVIDENCE_RECORDS) or byte_count+len(raw_line)>min(MAX_FILE_BYTES,MAX_EVIDENCE_BYTES):
                        truncated=True
                        stop_reason='EVIDENCE_LIMIT'
                        break
                    out.write(line)
                    digest.update(raw_line)
                    records+=1
                    byte_count+=len(raw_line)
                    if entry['kind']=='EVENT':
                        events+=1
                        if entry['event']=='HANDSHAKE_SUCCESS' and not sent:
                            for symbol in symbols:
                                await ws.send(json.dumps({'action':'SUBSCRIBE','type':'ORDER_BOOK',
                                    'data':{'symbol':symbol},'token':jwt},separators=(',',':')))
                            for symbol in trade_symbols:
                                await ws.send(json.dumps({'action':'SUBSCRIBE','type':'INSTRUMENT_TRADES',
                                     'data':{'symbol':symbol},'token':jwt},separators=(',',':')))
                            sent=True
                        elif entry['event']=='CONNECTION_CLOSED':
                            stop_reason='SERVER_CONNECTION_CLOSED'
                            replay.invalidate_all(stop_reason)
                            break
                    elif entry['kind']=='ERROR':
                        errors+=1
                        stop_reason='BROKER_EVENT_ERROR'
                        replay.invalidate_all(stop_reason)
                    elif entry['kind']=='TRADE_PRINTS':
                        ntrades+=1
                        trade_prints+=len(entry['payload']['trades'])
                    elif entry['kind']=='DATA':
                        ndata+=1
                        last_data_utc=received.isoformat()
                        books=entry['payload'].get('order_book',[])
                        for b in books:
                            book_rows+=len(b.get('rows',[]))
                            if b.get('is_data_snapshot') is True:
                                snapshots+=1
                        replay.on_sanitized_event(entry,received)
                    if errors>=5:
                        stop_reason='ERROR_BUDGET_REACHED'
                        break
    except Exception as exc:
        if isinstance(exc,ControlledFailure):
            raise
        raise ControlledFailure('FINAM_WEBSOCKET_CONNECTION_FAILED') from None
    # Output numbers are telemetry, not proof of executable prices/profit.
    views=[replay.view(s) for s in symbols]
    valid_top=sum(v['bid'] is not None and v['ask'] is not None for v in views)
    last_status={v['symbol']:v['status'] for v in views}
    status=('BOUNDED_OUT' if truncated else
            'DATA_RECORDED_WITH_ERRORS_UNVERIFIED' if ndata and errors else
            'STREAM_ERRORS_UNVERIFIED' if errors else
            'DATA_RECORDED' if ndata else
            'TRADE_PRINTS_ONLY_BOOK_NOT_VERIFIED' if ntrades else
            'SUBSCRIPTIONS_SENT_NO_MARKET_DATA' if sent else 'NO_HANDSHAKE')
    return {
        'schema':SCHEMA_VERSION,'started_utc':started,
        'ended_utc':datetime.now(timezone.utc).isoformat(),
        'subscribed':sent,'subscriptions_sent':len(symbols)+len(trade_symbols) if sent else 0,
        'symbols_count':len(symbols),'market_messages':ndata,
        'market_trade_messages':ntrades,'public_market_trades':trade_prints,
        'book_rows_received':book_rows,'ws_snapshots_seen':snapshots,
        'orderbook_tops_reconstructed':valid_top,
        'book_quality_by_symbol':last_status,
        'missing_or_unrecognized_data_frames':unknown,
        'unsolicited_data_frames':unsolicited,
        'last_data_utc':last_data_utc,
        'events':events,'errors':errors,'file':str(destination),
        'sha256':digest.hexdigest(),'records':records,'bytes':byte_count,
        'limits_reached':truncated,'stop_reason':stop_reason,
        'orders':0,'realized_pnl':0,
        'continuity_verified':False,'executable':False,'profit_proven':False,
        'status':status,
    }

def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NORM ARB V7 short read-only Finam WebSocket capture")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--trade-symbols", default=None,
                        help="Comma-separated public RTSX trade symbols; empty disables; default eligible WHEAT")
    parser.add_argument("--output-dir", default="evidence")
    args = parser.parse_args(argv)
    try:
        symbols = validate_symbols(args.symbols)
        trade_symbols = select_trade_symbols(symbols, args.trade_symbols)
        if not 1 <= args.seconds <= MAX_SECONDS:
            raise ControlledFailure("DURATION_OUT_OF_BOUNDS")
        api_secret = os.environ.get("FINAM_READONLY_API_KEY", "")
        jwt = authorize_readonly(api_secret)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = Path(args.output_dir) / ("finam_ws_" + timestamp + ".jsonl")
        summary = asyncio.run(capture_stream(jwt, symbols, args.seconds, destination,
                                                 trade_symbols=trade_symbols))
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["status"] == "DATA_RECORDED" else 2
    except ControlledFailure as exc:
        print(json.dumps({"status": str(exc), "orders": 0, "realized_pnl": 0}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(run_cli())