"""NORM ARB V7: causal, read-only, conservative shadow event replay.

Research-only interpretation of EXISTING sanitized Finam ORDER_BOOK and
INSTRUMENT_TRADES JSONL. It neither connects to a broker nor sends orders.
A public trade-through is NOT evidence of our hypothetical broker fill.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import argparse
import json
import re

from book_replay import BookReplay

MAX_TAPE_BYTES = 15_000_000
MAX_TAPE_EVENTS = 12_000
TRADE_HASH = re.compile(r"^[0-9a-f]{24}$")
SYMBOL = re.compile(r"^[A-Z0-9_.-]{2,40}@RTSX$")


class ShadowError(ValueError):
    """Error codes only: never echo raw brokerage frames or account data."""


def dec(value: Any, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if value is None or isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ShadowError("DECIMAL_NOT_VERIFIED")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, OverflowError):
        raise ShadowError("DECIMAL_INVALID") from None
    if not number.is_finite() or (positive and number <= 0) or (nonnegative and number < 0):
        raise ShadowError("DECIMAL_OUT_OF_RANGE")
    return number


def utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ShadowError("TIMESTAMP_MISSING")
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ShadowError("TIMESTAMP_INVALID") from None
    if d.tzinfo is None:
        raise ShadowError("TIMESTAMP_OFFSET_MISSING")
    return d.astimezone(timezone.utc)


@dataclass(frozen=True)
class ShadowConfig:
    symbol: str
    trading_session: str
    rub_per_spread_point: Decimal
    lots: Decimal = Decimal("1")
    min_width_points: Decimal = Decimal("1")
    latency_ms: int = 250
    max_level_age_ms: int = 2000
    max_print_delay_ms: int = 2000
    max_order_wait_ms: int = 90000
    max_position_hold_ms: int = 300000
    max_cycles: int = 50
    capital_rub: Decimal | None = None
    margin_rub: Decimal | None = None
    reserve_rub: Decimal = Decimal("0")
    broker_roundtrip_rub: Decimal | None = None
    maker_entry_exchange_fee_rub: Decimal | None = None
    maker_exit_exchange_fee_rub: Decimal | None = None
    taker_exit_exchange_fee_rub: Decimal | None = None
    actual_account_costs_verified: bool = False
    public_trade_units_verified: bool = False
    aggressor_side_semantics_verified: bool = False
    native_broker_order_route_verified: bool = False

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not SYMBOL.fullmatch(self.symbol):
            raise ShadowError("INSTRUMENT_INVALID")
        if not isinstance(self.trading_session, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}:[A-Z0-9_-]{2,24}", self.trading_session):
            raise ShadowError("EXPLICIT_SESSION_REQUIRED")
        if dec(self.lots, positive=True) != dec(self.lots).to_integral_value():
            raise ShadowError("LOTS_MUST_BE_INTEGRAL")
        dec(self.rub_per_spread_point, positive=True)
        dec(self.min_width_points, nonnegative=True)
        dec(self.reserve_rub, nonnegative=True)
        for name in ("latency_ms", "max_level_age_ms", "max_print_delay_ms",
                     "max_order_wait_ms", "max_position_hold_ms", "max_cycles"):
            v = getattr(self, name)
            if type(v) is not int or v < 1 or v > (1_000_000 if name != "max_cycles" else 1000):
                raise ShadowError("TIMING_OR_COUNT_INVALID")
        if self.capital_rub is not None:
            dec(self.capital_rub, positive=True)
        if self.margin_rub is not None:
            dec(self.margin_rub, positive=True)
        for name in ("broker_roundtrip_rub", "maker_entry_exchange_fee_rub",
                     "maker_exit_exchange_fee_rub", "taker_exit_exchange_fee_rub"):
            value = getattr(self, name)
            if value is not None:
                dec(value, nonnegative=True)
        for name in ("actual_account_costs_verified", "public_trade_units_verified",
                     "aggressor_side_semantics_verified", "native_broker_order_route_verified"):
            if type(getattr(self, name)) is not bool:
                raise ShadowError("VERIFICATION_FLAGS_INVALID")

    def maker_cost(self) -> Decimal | None:
        if not self.actual_account_costs_verified:
            return None
        parts = (self.broker_roundtrip_rub,
                 self.maker_entry_exchange_fee_rub,
                 self.maker_exit_exchange_fee_rub)
        if any(x is None for x in parts):
            return None
        return sum((dec(x, nonnegative=True) for x in parts), Decimal(0))

    def forced_taker_cost(self) -> Decimal | None:
        if not self.actual_account_costs_verified:
            return None
        parts = (self.broker_roundtrip_rub,
                 self.maker_entry_exchange_fee_rub,
                 self.taker_exit_exchange_fee_rub)
        if any(x is None for x in parts):
            return None
        return sum((dec(x, nonnegative=True) for x in parts), Decimal(0))


@dataclass
class HypOrder:
    side: str
    price: Decimal
    at: datetime
    qty: Decimal
    queue_ahead: Decimal
    model_filled: Decimal = Decimal(0)
    print_ids: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.qty - self.model_filled


@dataclass
class HypPosition:
    entry_price: Decimal
    qty: Decimal
    entered_at: datetime
    entry_print_ids: tuple[str, ...]


def read_sanitized_tape(path: Path, *, expected_sha256: str | None = None) -> tuple[list[dict], dict]:
    """Read ONLY bounded local, sanitized collector JSONL. Never fetch or run code."""
    if not isinstance(path, Path) or not path.is_file() or not 0 < path.stat().st_size <= MAX_TAPE_BYTES:
        raise ShadowError("TAPE_MISSING_EMPTY_OR_OVERSIZE")
    blob = path.read_bytes()
    digest = sha256(blob).hexdigest()
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
            raise ShadowError("EXPECTED_HASH_INVALID")
        if digest != expected_sha256:
            raise ShadowError("TAPE_HASH_MISMATCH")
    events = []
    for line in blob.splitlines():
        if not line or len(events) >= MAX_TAPE_EVENTS:
            raise ShadowError("TAPE_LINE_COUNT_INVALID")
        try:
            o = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeDecodeError):
            raise ShadowError("TAPE_JSON_INVALID") from None
        if not isinstance(o, dict) or o.get("kind") not in ("EVENT", "ERROR", "DATA", "TRADE_PRINTS"):
            raise ShadowError("TAPE_NOT_SANITIZED_V7")
        if set(o) - {"kind", "event", "error_code", "captured_utc", "schema",
                      "provider_timestamp", "subscription_type", "payload"}:
            raise ShadowError("TAPE_UNEXPECTED_OR_PRIVATE_FIELDS")
        events.append(o)
    return events, {"sha256": digest, "hash_matched_external_receipt": expected_sha256 is not None,
                    "event_count": len(events)}


def replay_sanitized_events(events: Iterable[dict[str, Any]], config: ShadowConfig,
                            *, trusted_receipt: bool = False,
                            collector_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Causally replay ONE recording. No future information, no broker execution.

    Default configuration cannot produce even a conditional fill: trade size and
    aggressor-side semantics must be independently confirmed before opting in.
    """
    config.validate()
    book = BookReplay((config.symbol,))
    book_view = book.view(config.symbol)
    pending: HypOrder | None = None
    pos: HypPosition | None = None
    state = "IDLE"
    observed_trades: dict[str, tuple[str, str, str, str]] = {}
    used_trades: set[str] = set()
    orders_proposed = 0
    cancels = 0
    accepted_book_events = 0
    accepted_trade_events = 0
    ignored_trade_events = 0
    proxy_partial_quantity = Decimal(0)
    completed: list[dict[str, Any]] = []
    forced: list[dict[str, Any]] = []
    quality = []
    seen_any = False
    prev_event_at: datetime | None = None
    latest_book_at: datetime | None = None
    latest_bid: Decimal | None = None
    min_mark: Decimal | None = None
    max_mark: Decimal | None = None

    capital_gate = "UNVERIFIED_MARGIN_OR_CAPITAL"
    if config.margin_rub is not None and config.capital_rub is not None:
        available = dec(config.capital_rub) - dec(config.reserve_rub)
        needed = dec(config.margin_rub) * dec(config.lots)
        capital_gate = "PASS_SCENARIO_ONLY" if available >= needed else "INSUFFICIENT_CAPITAL"

    def usable(view: dict) -> bool:
        return (view.get("status") == "TWO_SIDED_NO_SEQUENCE_OR_FILL_PROOF"
                and view.get("ws_snapshot_received") is True
                and view.get("bid") is not None and view.get("ask") is not None
                and view.get("bid_level_age_ms") is not None
                and view.get("ask_level_age_ms") is not None
                and 0 <= view["bid_level_age_ms"] <= config.max_level_age_ms
                and 0 <= view["ask_level_age_ms"] <= config.max_level_age_ms
                and dec(view["bid_qty"], positive=True) >= config.lots
                and dec(view["ask_qty"], positive=True) >= config.lots)

    def place(side: str, at: datetime, view: dict) -> HypOrder:
        nonlocal orders_proposed
        orders_proposed += 1
        if side == "BUY":
            return HypOrder(side, dec(view["bid"]), at, dec(config.lots), dec(view["bid_qty"]))
        return HypOrder(side, dec(view["ask"]), at, pos.qty, dec(view["ask_qty"]))

    def close_proxy(when: datetime, price: Decimal, ids: tuple[str, ...],
                    classification: str, *, full_two_print: bool) -> dict:
        gross = (price - pos.entry_price) * pos.qty * dec(config.rub_per_spread_point)
        cost = config.maker_cost() if full_two_print else config.forced_taker_cost()
        net = gross - cost * pos.qty if cost is not None else None
        return {"entry_at": pos.entered_at.isoformat(), "exit_at": when.isoformat(),
                "symbol": config.symbol, "lots": str(pos.qty),
                "entry_price": str(pos.entry_price), "exit_price": str(price),
                "entry_public_print_ids": list(pos.entry_print_ids),
                "exit_public_print_ids": list(ids),
                "gross_rub_proxy": str(gross),
                "net_rub_proxy_if_costs_verified": str(net) if net is not None else None,
                "classification": classification,
                "two_independent_public_print_sides": full_two_print,
                "broker_fills_proven": False}

    for idx, event in enumerate(events):
        if idx >= MAX_TAPE_EVENTS:
            quality.append("EVENT_COUNT_LIMIT")
            break
        if not isinstance(event, dict) or event.get("kind") not in ("EVENT", "DATA", "TRADE_PRINTS", "ERROR"):
            quality.append("RECORD_INVALID")
            break
        try:
            at = utc(event.get("captured_utc"))
        except ShadowError:
            quality.append("RECORD_TIME_INVALID")
            break
        if prev_event_at is not None and at < prev_event_at:
            quality.append("ARRIVAL_TIME_REVERSED")
            break
        prev_event_at = at
        seen_any = True
        kind = event["kind"]

        if kind == "ERROR" or (kind == "EVENT" and event.get("event") == "CONNECTION_CLOSED"):
            quality.append("PROVIDER_STREAM_INTERRUPTED")
            book.invalidate_all("STREAM_INTERRUPTED")
            break
        if kind == "EVENT":
            continue

        if kind == "DATA":
            if event.get("subscription_type") != "ORDER_BOOK":
                quality.append("UNRECOGNIZED_MARKET_EVENT")
                break
            try:
                vals = book.on_sanitized_event(event, at)
                book_view = book.view(config.symbol)
            except Exception:
                quality.append("BOOK_REPLAY_REJECTED")
                break
            if not vals:
                continue
            accepted_book_events += 1
            latest_book_at = at
            if not usable(book_view):
                # Do not infer fills from a stale, broken, unsnapshotted book.
                if pending is not None:
                    cancels += 1
                    pending = None
                if pos is not None:
                    quality.append("OPEN_POSITION_WITH_UNRELIABLE_BOOK")
                    break
                state = "IDLE"
                continue
            latest_bid = dec(book_view["bid"])
            if pos is not None:
                mark = (latest_bid - pos.entry_price) * pos.qty * dec(config.rub_per_spread_point)
                min_mark = mark if min_mark is None else min(mark, min_mark)
                max_mark = mark if max_mark is None else max(mark, max_mark)
                if (at - pos.entered_at).total_seconds() * 1000 >= config.max_position_hold_ms:
                    if dec(book_view["bid_qty"]) >= pos.qty:
                        forced.append(close_proxy(at, latest_bid, (), "FORCED_TAKER_BBO_ONLY_NO_PRINT", full_two_print=False))
                        pos = None
                        pending = None
                        state = "IDLE"
                        continue
                    quality.append("FORCED_EXIT_DEPTH_NOT_VERIFIED")
                    break
            if pending is not None:
                visible = dec(book_view["bid"]) if pending.side == "BUY" else dec(book_view["ask"])
                if visible != pending.price:
                    if pending.model_filled > 0:
                        quality.append("PARTIAL_POSITION_PRICE_MOVED_UNRESOLVED")
                        break
                    pending = None
                    cancels += 1
                    state = "IDLE" if pos is None else "WAIT_EXIT_BOOK"
                elif (at - pending.at).total_seconds() * 1000 >= config.max_order_wait_ms:
                    if pending.model_filled > 0:
                        quality.append("PARTIAL_EXECUTION_TIMED_OUT_UNRESOLVED")
                        break
                    pending = None
                    cancels += 1
                    state = "IDLE" if pos is None else "WAIT_EXIT_BOOK"
            if pending is None and capital_gate != "INSUFFICIENT_CAPITAL":
                if state == "WAIT_EXIT_BOOK" and pos is not None:
                    pending = place("SELL", at, book_view)
                    state = "WAIT_EXIT"
                elif state == "IDLE" and pos is None and len(completed) < config.max_cycles:
                    width = dec(book_view["ask"]) - dec(book_view["bid"])
                    if width >= dec(config.min_width_points):
                        pending = place("BUY", at, book_view)
                        state = "WAIT_ENTRY"
            continue

        if kind != "TRADE_PRINTS" or event.get("subscription_type") != "INSTRUMENT_TRADES":
            quality.append("UNRECOGNIZED_TRADE_ENVELOPE")
            break
        p = event.get("payload")
        if not isinstance(p, dict) or not isinstance(p.get("trades"), list):
            quality.append("BAD_TRADE_PAYLOAD")
            break
        if p.get("symbol") != config.symbol:
            continue
        for trade in p["trades"]:
            accepted_trade_events += 1
            if not isinstance(trade, dict):
                ignored_trade_events += 1
                continue
            try:
                price = dec(trade["price"])
                volume = dec(trade["size"], positive=True)
                trade_at = utc(trade["timestamp"])
                tid = trade.get("public_trade_id_hash")
                side = trade.get("side")
            except (KeyError, ShadowError):
                ignored_trade_events += 1
                continue
            if not isinstance(tid, str) or not TRADE_HASH.fullmatch(tid):
                ignored_trade_events += 1
                continue
            fingerprint = (str(price), str(volume), trade_at.isoformat(), str(side))
            prior = observed_trades.setdefault(tid, fingerprint)
            if prior != fingerprint:
                quality.append("CONFLICTING_EXCHANGE_TRADE_ID")
                break
            if tid in used_trades:
                ignored_trade_events += 1
                continue
            used_trades.add(tid)
            if (trade_at > at or (at - trade_at).total_seconds() * 1000 > config.max_print_delay_ms
                    or latest_book_at is None or latest_book_at > at
                    or pending is None):
                ignored_trade_events += 1
                continue
            if not (config.public_trade_units_verified and config.aggressor_side_semantics_verified):
                ignored_trade_events += 1
                continue
            direction = "SIDE_SELL" if pending.side == "BUY" else "SIDE_BUY"
            if side not in (direction, direction.removeprefix("SIDE_")):
                ignored_trade_events += 1
                continue
            if not (price < pending.price if pending.side == "BUY" else price > pending.price):
                # A touch at own limit is NOT a fill.
                ignored_trade_events += 1
                continue
            threshold = pending.at + timedelta(milliseconds=config.latency_ms)
            if at <= threshold or trade_at <= threshold:
                ignored_trade_events += 1
                continue
            # The queue in front is NOT known exactly; this deliberately assumes
            # its full visible quantity is ahead, but is still just a proxy.
            consumed_queue = min(volume, pending.queue_ahead)
            pending.queue_ahead -= consumed_queue
            remaining_volume = volume - consumed_queue
            take = min(remaining_volume, pending.remaining)
            if take > 0:
                pending.model_filled += take
                proxy_partial_quantity += take
                pending.print_ids.append(tid)
                if pending.remaining == 0:
                    if pending.side == "BUY":
                        pos = HypPosition(pending.price, pending.qty, at, tuple(pending.print_ids))
                        pending = None
                        state = "WAIT_EXIT_BOOK"
                    else:
                        completed.append(close_proxy(at, pending.price, tuple(pending.print_ids),
                                                     "CONDITIONAL_TWO_MAKER_PRINT_THROUGH_NO_BROKER_FILL",
                                                     full_two_print=True))
                        pos = None
                        pending = None
                        state = "IDLE"
            if quality:
                break
        if quality:
            break

    unresolved = False
    open_qty = Decimal(0)
    if pending is not None and pending.model_filled:
        if pending.side == "BUY":
            open_qty = pending.model_filled
            unresolved = True
        else:
            open_qty = pos.qty - pending.model_filled if pos is not None else Decimal(0)
            unresolved = open_qty > 0
    elif pos is not None:
        open_qty = pos.qty
        unresolved = True

    if not seen_any:
        quality.append("EMPTY_MARKET_TAPE")
    if collector_metrics is None:
        quality.append("NO_COLLECTOR_LOSS_METRICS")
    else:
        if (collector_metrics.get("errors") != 0
                or collector_metrics.get("missing_or_unrecognized_data_frames") != 0
                or collector_metrics.get("limits_reached") is not False):
            quality.append("COLLECTOR_LOSS_OR_TRUNCATION")
    if not trusted_receipt:
        quality.append("SHA256_NOT_EXTERNALLY_ATTESTED")
    if not config.public_trade_units_verified or not config.aggressor_side_semantics_verified:
        quality.append("TRADE_QUANTITY_OR_AGGRESSOR_SEMANTICS_NOT_VERIFIED")
    if config.maker_cost() is None:
        quality.append("ACTUAL_ACCOUNT_COMMISSION_UNKNOWN")
    if not config.native_broker_order_route_verified:
        quality.append("NATIVE_ORDER_ROUTE_UNKNOWN")
    # Finam sanitized book messages do NOT carry an authenticated gapless
    # exchange sequence, hence even complete conditional replay is NOT LIVE proof.
    quality.append("EXCHANGE_ORDERBOOK_SEQUENCE_NOT_VERIFIABLE")

    net_values = [Decimal(x["net_rub_proxy_if_costs_verified"]) for x in completed
                  if x["net_rub_proxy_if_costs_verified"] is not None]
    net_proxy = sum(net_values, Decimal(0)) if len(net_values) == len(completed) and completed else None
    return {
        "kind": "NORM_SHADOW_REPLAY_V1", "symbol": config.symbol,
        "trading_session": config.trading_session,
        "status": ("OPEN_OR_PARTIALLY_FILLED_RISK_NOT_CLOSED" if unresolved
                   else "CONDITIONAL_COMPLETED_CYCLES_UNVERIFIED_FILLS" if completed
                   else "INSUFFICIENT_EVIDENCE_NO_CLOSED_CYCLES"),
        "source_attested": trusted_receipt,
        "capital_gate": capital_gate,
        "orderbook_messages_replayed": accepted_book_events,
        "public_trade_rows_received": accepted_trade_events,
        "public_trade_rows_ignored": ignored_trade_events,
        "unique_public_trade_ids": len(observed_trades),
        "hypothetical_orders_posted": orders_proposed,
        "hypothetical_orders_cancelled": cancels,
        "conditionally_closed_two_print_cycles": len(completed),
        "conditional_cycle_rows": completed,
        "forced_exit_bbo_only_scenarios": forced,
        "open_conditional_lots": str(open_qty),
        "unresolved_exposure": unresolved,
        "proxy_gross_mark_min_rub": str(min_mark) if min_mark is not None else None,
        "proxy_gross_mark_max_rub": str(max_mark) if max_mark is not None else None,
        "conditional_net_rub_if_fees_verified": str(net_proxy) if net_proxy is not None else None,
        "invalid_or_unverified_evidence_reasons": sorted(set(quality)),
        "actual_orders": 0,
        "actual_broker_fills": 0,
        "realized_pnl_rub": None,
        "profitability_proven": False,
        "broker_execution_emulation": "CONSERVATIVE_QUEUE_PROXY_NOT_PROOF",
    }


def replay_file(evidence: Path, config: ShadowConfig, *, expected_sha256: str | None = None,
                collector_metrics: dict | None = None) -> dict:
    events, provenance = read_sanitized_tape(evidence, expected_sha256=expected_sha256)
    return {"input_provenance": provenance, **replay_sanitized_events(
        events, config, trusted_receipt=provenance["hash_matched_external_receipt"],
        collector_metrics=collector_metrics)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline-only NORM ARB V7 shadow market-event replay")
    parser.add_argument("--evidence", required=True, help="Existing sanitized Finam JSONL only")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--session", required=True, help="Explicit exchange trading session YYYY-MM-DD:RTSX")
    parser.add_argument("--rub-per-point", required=True)
    parser.add_argument("--min-width", default="1")
    parser.add_argument("--sha256", default=None)
    args = parser.parse_args(argv)
    try:
        cfg = ShadowConfig(symbol=args.symbol, trading_session=args.session,
                           rub_per_spread_point=dec(args.rub_per_point, positive=True),
                           min_width_points=dec(args.min_width, nonnegative=True))
        result = replay_file(Path(args.evidence), cfg, expected_sha256=args.sha256)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result["status"] != "OPEN_OR_PARTIALLY_FILLED_RISK_NOT_CLOSED" else 2
    except (ShadowError, OSError):
        print(json.dumps({"status": "OFFLINE_INPUT_REJECTED", "actual_orders": 0,
                          "realized_pnl_rub": None}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
