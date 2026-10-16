"""Conservative finite-depth reconstruction of Finam RTSX books (read-only).

Finam StreamOrderBook.Row uses protobuf oneof side: rows usually carry ONE
quantity, buy_size OR sell_size. Unmentioned side MUST survive every update.
A full WS is_data_snapshot may bootstrap a book. REST bootstrap is also
supported as a separate, explicitly unsequenced diagnostic base. A stream
break invalidates every book. No official gapless sequence guarantee exists;
therefore no reconstructed book is labelled executable or fill-proven.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_LEVELS = 100
MAX_ROWS_PER_MESSAGE = 2 * MAX_LEVELS
MAX_LEVEL_AGE_MS = 2000
VALID_ACTIONS = frozenset(("ACTION_ADD", "ACTION_UPDATE", "ACTION_REMOVE"))
SNAPSHOT_ACTIONS = VALID_ACTIONS | {"ACTION_UNSPECIFIED", None}

class InvalidMarketData(ValueError):
    """Never contain raw market frame or account info in exception messages."""


def _decimal(v: Any, *, quantity: bool = False) -> Decimal:
    if isinstance(v, dict):
        if len(v) != 1 or 'value' not in v:
            raise InvalidMarketData('UNEXPECTED_DECIMAL_STRUCTURE')
        v = v['value']
    if v is None or type(v) is bool or not isinstance(v, (str, int, Decimal)):
        raise InvalidMarketData('INVALID_DECIMAL')
    try:
        out = Decimal(str(v))
    except (InvalidOperation, ValueError, OverflowError):
        raise InvalidMarketData('INVALID_DECIMAL') from None
    if not out.is_finite() or (quantity and out < 0):
        raise InvalidMarketData('NONFINITE_OR_NEGATIVE')
    return out


def _datetime(v: Any) -> datetime | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise InvalidMarketData('INVALID_LEVEL_TIME')
    try:
        result = datetime.fromisoformat(v.replace('Z','+00:00'))
    except ValueError:
        raise InvalidMarketData('INVALID_LEVEL_TIME') from None
    if result.tzinfo is None:
        raise InvalidMarketData('LEVEL_TIME_NOT_UTC')
    return result.astimezone(timezone.utc)


def _recv(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidMarketData('INVALID_RECEIVE_CLOCK')
    return value.astimezone(timezone.utc)


def _side(row: dict[str, Any], snake: str, camel: str) -> tuple[bool, Decimal | None]:
    if snake in row and camel in row and row[snake] != row[camel]:
        raise InvalidMarketData('CONFLICTING_SIDE_ALIASES')
    if snake not in row and camel not in row:
        return False, None
    value = row[camel] if camel in row else row[snake]
    return True, _decimal(value, quantity=True)


@dataclass(frozen=True)
class Level:
    qty: Decimal
    updated_at: datetime | None


@dataclass
class Book:
    symbol: str
    bids: dict[Decimal, Level] = field(default_factory=dict)
    asks: dict[Decimal, Level] = field(default_factory=dict)
    rest_bootstrapped: bool = False
    ws_snapshot: bool = False
    invalidated: bool = False
    invalid_reason: str | None = None
    updates: int = 0
    snapshots: int = 0
    last_provider_timestamp: int | None = None
    last_received_at: datetime | None = None
    last_snapshot_at: datetime | None = None


class BookReplay:
    def __init__(self, symbols: tuple[str, ...]):
        if not isinstance(symbols, tuple) or not (1 <= len(symbols) <= 6) or len(symbols) != len(set(symbols)):
            raise InvalidMarketData('INVALID_UNIVERSE')
        if any(type(s) is not str or not s.endswith('@RTSX') or len(s)>45 for s in symbols):
            raise InvalidMarketData('INVALID_SYMBOL')
        self.books = {symbol: Book(symbol) for symbol in symbols}

    @staticmethod
    def _copy(book: Book) -> Book:
        return Book(symbol=book.symbol, bids=dict(book.bids), asks=dict(book.asks),
            rest_bootstrapped=book.rest_bootstrapped, ws_snapshot=book.ws_snapshot,
            invalidated=book.invalidated, invalid_reason=book.invalid_reason,
            updates=book.updates, snapshots=book.snapshots,
            last_provider_timestamp=book.last_provider_timestamp,
            last_received_at=book.last_received_at, last_snapshot_at=book.last_snapshot_at)

    @staticmethod
    def _check_depth(book: Book) -> None:
        if len(book.bids) > MAX_LEVELS or len(book.asks) > MAX_LEVELS:
            raise InvalidMarketData('BOOK_DEPTH_EXCEEDED')
        if book.bids and book.asks and max(book.bids) >= min(book.asks):
            raise InvalidMarketData('CROSSED_OR_LOCKED_BOOK')

    @staticmethod
    def _apply_full_row(book: Book, row: dict[str, Any], *, action: str | None) -> None:
        if not isinstance(row, dict):
            raise InvalidMarketData('INVALID_BOOK_ROW')
        price = _decimal(row.get('price'))
        has_buy, buy = _side(row, 'buy_size', 'buySize')
        has_sell, sell = _side(row, 'sell_size', 'sellSize')
        if not has_buy and not has_sell:
            raise InvalidMarketData('AMBIGUOUS_SIDE_NOT_PRESENT')
        if action == 'ACTION_REMOVE':
            if has_buy:
                book.bids.pop(price, None)
            if has_sell:
                book.asks.pop(price, None)
            return
        at = _datetime(row.get('timestamp'))
        if has_buy:
            if buy > 0:
                book.bids[price] = Level(buy,at)
            else:
                book.bids.pop(price,None)
        if has_sell:
            if sell > 0:
                book.asks[price] = Level(sell,at)
            else:
                book.asks.pop(price,None)

    @staticmethod
    def _validate_rows(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows,list) or not (1<=len(rows)<=MAX_ROWS_PER_MESSAGE):
            raise InvalidMarketData('MISSING_OR_OVERSIZED_ROWS')
        if any(not isinstance(row, dict) for row in rows):
            raise InvalidMarketData('INVALID_BOOK_ROW')
        return rows

    def invalidate(self, symbol: str, reason: str = 'STREAM_INTERRUPTED') -> None:
        if symbol not in self.books:
            raise InvalidMarketData('UNAPPROVED_SYMBOL')
        book=self.books[symbol]
        book.invalidated=True
        book.invalid_reason=reason
        book.rest_bootstrapped=False
        book.ws_snapshot=False
        book.bids.clear()
        book.asks.clear()

    def invalidate_all(self, reason: str = 'STREAM_INTERRUPTED') -> None:
        for sym in self.books:
            self.invalidate(sym,reason)

    def _snapshot(self, symbol: str, rows: list[dict[str,Any]], received_at: datetime,
                  source: str, timestamp: int | None = None) -> dict[str,Any]:
        if symbol not in self.books:
            raise InvalidMarketData('UNAPPROVED_SYMBOL')
        received=_recv(received_at)
        incoming=Book(symbol=symbol,rest_bootstrapped=True,ws_snapshot=(source=='WS'),
                      last_received_at=received,last_snapshot_at=received,
                      last_provider_timestamp=timestamp,
                      snapshots=self.books[symbol].snapshots+1)
        try:
            rows=self._validate_rows(rows)
            for row in rows:
                act=row.get('action')
                if act not in SNAPSHOT_ACTIONS or act == 'ACTION_REMOVE':
                    raise InvalidMarketData('INVALID_SNAPSHOT_ACTION')
                self._apply_full_row(incoming,row,action=None)
            self._check_depth(incoming)
        except InvalidMarketData:
            self.invalidate(symbol,'INVALID_SNAPSHOT')
            raise
        self.books[symbol]=incoming
        return self.view(symbol)

    def bootstrap_rest(self, symbol: str, response: dict[str,Any], received_at: datetime) -> dict[str,Any]:
        container = response.get('orderbook') if isinstance(response,dict) else None
        rows = container.get('rows') if isinstance(container,dict) else None
        return self._snapshot(symbol,rows,received_at,'REST')

    def bootstrap_ws(self, symbol: str, payload: dict[str,Any], received_at:datetime,
                     provider_timestamp: int|None = None) -> dict[str,Any]:
        if payload.get('is_data_snapshot') is not True and payload.get('isDataSnapshot') is not True:
            self.invalidate(symbol,'WS_NOT_SNAPSHOT')
            return self.view(symbol)
        if provider_timestamp is not None and (type(provider_timestamp) is not int or not 0 <= provider_timestamp < 2**53):
            self.invalidate(symbol,'INVALID_ENVELOPE_TIMESTAMP')
            return self.view(symbol)
        return self._snapshot(symbol,payload.get('rows'),received_at,'WS',provider_timestamp)

    def apply_delta(self, symbol:str, payload:dict[str,Any], received_at:datetime,
                    provider_timestamp:int|None=None) -> dict[str,Any]:
        if symbol not in self.books:
            raise InvalidMarketData('UNAPPROVED_SYMBOL')
        book=self.books[symbol]
        if book.invalidated or not book.rest_bootstrapped:
            return self.view(symbol)
        received=_recv(received_at)
        if provider_timestamp is not None and (type(provider_timestamp) is not int or not 0 <= provider_timestamp < 2**53):
            self.invalidate(symbol,'INVALID_ENVELOPE_TIMESTAMP')
            return self.view(symbol)
        if book.last_provider_timestamp is not None and provider_timestamp is not None and provider_timestamp < book.last_provider_timestamp:
            self.invalidate(symbol,'OUT_OF_ORDER_PROVIDER_TIME')
            return self.view(symbol)
        updated=self._copy(book)
        try:
            rows=self._validate_rows(payload.get('rows') if isinstance(payload,dict) else None)
            for row in rows:
                action=row.get('action')
                if action not in VALID_ACTIONS:
                    raise InvalidMarketData('AMBIGUOUS_WS_ACTION')
                self._apply_full_row(updated,row,action=action)
            self._check_depth(updated)
        except InvalidMarketData as exc:
            self.invalidate(symbol,str(exc))
            return self.view(symbol)
        updated.updates += 1
        if provider_timestamp is not None:
            updated.last_provider_timestamp=provider_timestamp
        updated.last_received_at=received
        self.books[symbol]=updated
        return self.view(symbol)

    def on_sanitized_event(self, event:dict[str,Any], received_at:datetime) -> list[dict[str,Any]]:
        """Ingest only sanitized ORDER_BOOK data. Each symbol has separate validity."""
        if not isinstance(event,dict) or event.get('kind') != 'DATA' or event.get('subscription_type')!='ORDER_BOOK':
            return []
        payload=event.get('payload')
        books=payload.get('order_book') if isinstance(payload,dict) else None
        if not isinstance(books,list):
            return []
        result=[]
        for item in books:
            sym=item.get('symbol') if isinstance(item,dict) else None
            if sym not in self.books:
                continue
            try:
                if item.get('is_data_snapshot') is True:
                    result.append(self.bootstrap_ws(sym,item,received_at,event.get('provider_timestamp')))
                else:
                    result.append(self.apply_delta(sym,item,received_at,event.get('provider_timestamp')))
            except InvalidMarketData:
                self.invalidate(sym,'WS_FRAME_INVALID')
                result.append(self.view(sym))
        return result

    def view(self,symbol:str) -> dict[str,Any]:
        if symbol not in self.books:
            raise InvalidMarketData('UNAPPROVED_SYMBOL')
        b=self.books[symbol]
        result={'symbol':symbol,'status':'UNBOOTSTRAPPED',
                'market_data_observed':False,'continuity_verified':False,
                'executable':False,'realized_pnl':None,'updates':b.updates,
                'snapshots':b.snapshots,'ws_snapshot_received':b.ws_snapshot,
                'bid':None,'ask':None,'bid_qty':None,'ask_qty':None,
                'bid_level_age_ms':None,'ask_level_age_ms':None,
                'reason':b.invalid_reason}
        if b.invalidated:
            result['status']='INVALIDATED_REQUIRES_REST_BOOTSTRAP'
            return result
        if not b.rest_bootstrapped:
            return result
        result['market_data_observed']=True
        if not b.bids or not b.asks:
            result['status']='ONE_SIDED'
            return result
        bp=max(b.bids);ap=min(b.asks);bid=b.bids[bp];ask=b.asks[ap]
        result.update(bid=str(bp),ask=str(ap),bid_qty=str(bid.qty),ask_qty=str(ask.qty))
        if b.last_received_at is None or bid.updated_at is None or ask.updated_at is None:
            result['status']='LEVEL_TIMESTAMP_UNVERIFIED'
            return result
        age_bid=round((b.last_received_at-bid.updated_at).total_seconds()*1000)
        age_ask=round((b.last_received_at-ask.updated_at).total_seconds()*1000)
        result['bid_level_age_ms']=age_bid;result['ask_level_age_ms']=age_ask
        if min(age_bid,age_ask)<-MAX_LEVEL_AGE_MS:
            result['status']='TIME_SKEW_UNVERIFIED'
        elif max(age_bid,age_ask)>MAX_LEVEL_AGE_MS:
            result['status']='LEVEL_TIMESTAMP_OLD_NOT_BOOK_STATUS'
        else:
            result['status']='TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF'
        return result