"""Conservative reconstruction of Finam native-spread books from REST + WS updates.

This is a data-quality gate, not an arbitrage engine. A completed two-sided
book NEVER proves FOK fillability, continuity, fees or positive net PnL.
All price/size arithmetic is Decimal; all operations fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

VALID_ACTIONS = {"ACTION_ADD", "ACTION_UPDATE", "ACTION_REMOVE"}
MAX_LEVELS = 100
MAX_LEVEL_AGE_MS = 2000


class InvalidMarketData(ValueError):
    """The upstream frame cannot safely update a normalized book."""


def _decimal(value: Any, *, quantity: bool = False) -> Decimal:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise InvalidMarketData("MISSING_OR_INVALID_DECIMAL")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise InvalidMarketData("MISSING_OR_INVALID_DECIMAL") from None
    if not number.is_finite() or (quantity and number < 0):
        raise InvalidMarketData("NONFINITE_OR_NEGATIVE_QUANTITY")
    return number


def _timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidMarketData("INVALID_LEVEL_TIMESTAMP") from None
        if result.tzinfo is None:
            raise InvalidMarketData("NAIVE_LEVEL_TIMESTAMP")
        return result.astimezone(timezone.utc)
    raise InvalidMarketData("INVALID_LEVEL_TIMESTAMP")


def _received(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidMarketData("RECEPTION_CLOCK_UNVERIFIED")
    return value.astimezone(timezone.utc)


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
    invalidated: bool = False
    invalid_reason: str | None = None
    updates: int = 0
    last_provider_timestamp: int | None = None
    last_received_at: datetime | None = None


class BookReplay:
    """In-memory proof-gated book for a small preapproved native-spread universe."""

    def __init__(self, symbols: tuple[str, ...]):
        if not symbols or len(symbols) > 6 or len(set(symbols)) != len(symbols):
            raise InvalidMarketData("INVALID_SYMBOL_UNIVERSE")
        if any(not isinstance(s, str) or not s.endswith("@RTSX") for s in symbols):
            raise InvalidMarketData("INVALID_RTSX_SYMBOL")
        self.books = {s: Book(symbol=s) for s in symbols}

    @staticmethod
    def _apply_full_row(book: Book, row: dict[str, Any], *, action: str | None) -> None:
        if not isinstance(row, dict):
            raise InvalidMarketData("INVALID_BOOK_ROW")
        price = _decimal(row.get("price"))
        if action == "ACTION_REMOVE":
            book.bids.pop(price, None)
            book.asks.pop(price, None)
            return
        # Snapshot and each ADD/UPDATE row must describe both sides at a price.
        # Never interpret an omitted quantity field as an explicit zero.
        buy = _decimal(row.get("buy_size"), quantity=True)
        sell = _decimal(row.get("sell_size"), quantity=True)
        at = _timestamp(row.get("timestamp"))
        if buy > 0:
            book.bids[price] = Level(buy, at)
        else:
            book.bids.pop(price, None)
        if sell > 0:
            book.asks[price] = Level(sell, at)
        else:
            book.asks.pop(price, None)

    @staticmethod
    def _copy(book: Book) -> Book:
        return Book(
            symbol=book.symbol, bids=dict(book.bids), asks=dict(book.asks),
            rest_bootstrapped=book.rest_bootstrapped, invalidated=book.invalidated,
            invalid_reason=book.invalid_reason, updates=book.updates,
            last_provider_timestamp=book.last_provider_timestamp,
            last_received_at=book.last_received_at,
        )

    @staticmethod
    def _check_depth(book: Book) -> None:
        if len(book.bids) > MAX_LEVELS or len(book.asks) > MAX_LEVELS:
            raise InvalidMarketData("BOOK_DEPTH_EXCEEDED")
        if book.bids and book.asks and max(book.bids) >= min(book.asks):
            raise InvalidMarketData("CROSSED_OR_LOCKED_BOOK")

    def bootstrap_rest(self, symbol: str, response: dict[str, Any], received_at: datetime) -> dict[str, Any]:
        """REST OrderBook is the ONLY initial base; a delta never fabricates it."""
        if symbol not in self.books:
            raise InvalidMarketData("UNAPPROVED_SYMBOL")
        received = _received(received_at)
        container = response.get("orderbook") if isinstance(response, dict) else None
        rows = container.get("rows") if isinstance(container, dict) else None
        if not isinstance(rows, list) or not rows or len(rows) > 2 * MAX_LEVELS:
            raise InvalidMarketData("REST_BOOTSTRAP_MISSING_OR_OVERSIZED")
        incoming = Book(symbol=symbol, rest_bootstrapped=True, last_received_at=received)
        try:
            for row in rows:
                self._apply_full_row(incoming, row, action=None)
            self._check_depth(incoming)
        except InvalidMarketData:
            # A failed bootstrap leaves existing state untrusted, never usable.
            self.invalidate(symbol, "REST_BOOTSTRAP_INVALID")
            raise
        self.books[symbol] = incoming
        return self.view(symbol)

    def apply_delta(self, symbol: str, payload: dict[str, Any], received_at: datetime,
                    provider_timestamp: int | None = None) -> dict[str, Any]:
        """Apply only explicit ADD/UPDATE/REMOVE against an earlier REST snapshot."""
        if symbol not in self.books:
            raise InvalidMarketData("UNAPPROVED_SYMBOL")
        book = self.books[symbol]
        if book.invalidated or not book.rest_bootstrapped:
            return self.view(symbol)
        received = _received(received_at)
        if provider_timestamp is not None and (
            not isinstance(provider_timestamp, int) or isinstance(provider_timestamp, bool) or provider_timestamp < 0
        ):
            self.invalidate(symbol, "INVALID_ENVELOPE_TIMESTAMP")
            return self.view(symbol)
        if (provider_timestamp is not None and book.last_provider_timestamp is not None
                and provider_timestamp < book.last_provider_timestamp):
            self.invalidate(symbol, "OUT_OF_ORDER_PROVIDER_TIME")
            return self.view(symbol)
        # There is NO sequence number in the verified WS envelope. Even if
        # timestamps do not regress, a dropped frame cannot be ruled out.
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows or len(rows) > 2 * MAX_LEVELS:
            self.invalidate(symbol, "WS_DELTA_MISSING_OR_OVERSIZED")
            return self.view(symbol)
        updated = self._copy(book)
        try:
            for row in rows:
                if not isinstance(row, dict) or row.get("action") not in VALID_ACTIONS:
                    raise InvalidMarketData("AMBIGUOUS_WS_ACTION")
                self._apply_full_row(updated, row, action=row["action"])
            self._check_depth(updated)
        except InvalidMarketData as exc:
            self.invalidate(symbol, str(exc))
            return self.view(symbol)
        updated.updates += 1
        updated.last_provider_timestamp = provider_timestamp or book.last_provider_timestamp
        updated.last_received_at = received
        self.books[symbol] = updated
        return self.view(symbol)

    def invalidate(self, symbol: str, reason: str = "STREAM_INTERRUPTED") -> None:
        if symbol not in self.books:
            raise InvalidMarketData("UNAPPROVED_SYMBOL")
        book = self.books[symbol]
        book.invalidated = True
        book.invalid_reason = reason

    def invalidate_all(self, reason: str = "STREAM_INTERRUPTED") -> None:
        for symbol in self.books:
            self.invalidate(symbol, reason)

    def view(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.books:
            raise InvalidMarketData("UNAPPROVED_SYMBOL")
        b = self.books[symbol]
        result: dict[str, Any] = {
            "symbol": symbol,
            "status": "UNBOOTSTRAPPED",
            "market_data_observed": False,
            "continuity_verified": False,
            "executable": False,
            "realized_pnl": None,
            "updates": b.updates,
            "bid": None,
            "ask": None,
            "bid_qty": None,
            "ask_qty": None,
            "bid_level_age_ms": None,
            "ask_level_age_ms": None,
            "reason": b.invalid_reason,
        }
        if b.invalidated:
            result["status"] = "INVALIDATED_REQUIRES_REST_BOOTSTRAP"
            return result
        if not b.rest_bootstrapped:
            return result
        result["market_data_observed"] = True
        if not b.bids or not b.asks:
            result["status"] = "ONE_SIDED"
            return result
        bp, ap = max(b.bids), min(b.asks)
        bid, ask = b.bids[bp], b.asks[ap]
        result.update(bid=str(bp), ask=str(ap),
                      bid_qty=str(bid.qty), ask_qty=str(ask.qty))
        if b.last_received_at is None or bid.updated_at is None or ask.updated_at is None:
            result["status"] = "LEVEL_TIMESTAMP_UNVERIFIED"
            return result
        age_bid = round((b.last_received_at-bid.updated_at).total_seconds()*1000)
        age_ask = round((b.last_received_at-ask.updated_at).total_seconds()*1000)
        result["bid_level_age_ms"] = age_bid
        result["ask_level_age_ms"] = age_ask
        if min(age_bid, age_ask) < -2000:
            result["status"] = "TIME_SKEW_UNVERIFIED"
        elif max(age_bid, age_ask) > MAX_LEVEL_AGE_MS:
            result["status"] = "LEVEL_TIMESTAMP_OLD_NOT_BOOK_STATUS"
        else:
            result["status"] = "TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF"
        return result
