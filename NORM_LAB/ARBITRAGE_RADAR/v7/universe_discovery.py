"""Offline RTSX calendar-spread triangle universe discovery from Finam assets.

Symbol-derived leg identity is only a HYPOTHESIS. Neither a catalogue record
nor a structurally complete triangle proves account access, pricing parity,
lot parity or an executable arbitrage. No network, secrets or trading.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections import defaultdict
from typing import Iterable, Any

_MONTHS = frozenset("FGHJKMNQUVXZ")
_YEAR = re.compile(r"^[FGHJKMNQUVXZ][0-9]{1,2}$")
_SYMBOL = re.compile(r"^[A-Z0-9]+@RTSX$")
_MAX_ASSETS = 125000


@dataclass(frozen=True)
class TentativeLegs:
    symbol: str
    root: str
    first_contract: str
    second_contract: str
    identity_status: str = "TICKER_PATTERN_ONLY_NOT_VERIFIED"


def infer_leg_symbols(symbol: str) -> TentativeLegs | None:
    """Conservative equal-root ticker split. Reject ambiguous splits outright.

    Example: W4V6W4X6@RTSX -> W4V6 and W4X6. This does NOT prove exchange
    multiplier, sign, settlement, futures leg order, or tradability.
    """
    if not isinstance(symbol, str) or len(symbol) > 68 or not _SYMBOL.fullmatch(symbol):
        return None
    ticker = symbol.rsplit("@", 1)[0]
    matches: list[TentativeLegs] = []
    for root_length in range(1, min(13, len(ticker))):
        root = ticker[:root_length]
        for n_year_digits in (1, 2):
            left_end = root_length + 1 + n_year_digits
            if left_end >= len(ticker):
                continue
            expiry1 = ticker[root_length:left_end]
            expiry2 = ticker[left_end + len(root):]
            if (not _YEAR.fullmatch(expiry1) or not ticker.startswith(root, left_end)
                    or not _YEAR.fullmatch(expiry2)):
                continue
            first, second = root+expiry1, root+expiry2
            if first != second:
                matches.append(TentativeLegs(symbol, root, first, second))
    # It is unsafe to select a convenient interpretation when the ticker is ambiguous.
    return matches[0] if len(matches) == 1 else None


def discover_spread_triangles(assets: Iterable[dict[str, Any]], *,
                              max_candidates: int = 300) -> dict[str, Any]:
    if not isinstance(max_candidates, int) or not 1 <= max_candidates <= 1000:
        raise ValueError("INVALID_OUTPUT_CAP")
    rows = list(assets)
    if len(rows) > _MAX_ASSETS:
        raise ValueError("ASSET_CATALOG_TOO_LARGE")
    instruments: dict[str, TentativeLegs] = {}
    rejected = defaultdict(int)
    for entry in rows:
        if not isinstance(entry, dict):
            rejected["INVALID_CATALOG_ROW"] += 1
            continue
        if (entry.get("mic") != "RTSX" or entry.get("type") != "SPREADS"
                or entry.get("is_archived") is not False):
            continue
        sym = entry.get("symbol")
        result = infer_leg_symbols(sym)
        if result is None:
            rejected["UNPARSEABLE_TICKER"] += 1
            continue
        if sym in instruments:
            rejected["DUPLICATE_SYMBOL"] += 1
            continue
        instruments[sym] = result

    by_pair: dict[tuple[str, str, str], TentativeLegs] = {}
    by_start: dict[tuple[str, str], list[TentativeLegs]] = defaultdict(list)
    for x in instruments.values():
        key = (x.root, x.first_contract, x.second_contract)
        if key in by_pair:
            rejected["DUPLICATE_TENTATIVE_CONTRACT_PAIR"] += 1
            continue
        by_pair[key] = x
        by_start[(x.root, x.first_contract)].append(x)

    full: set[tuple[str, str, str]] = set()
    missing: set[tuple[str, str, str]] = set()
    chains = 0
    for first in by_pair.values():
        for second in by_start.get((first.root, first.second_contract), []):
            if first.first_contract == second.second_contract:
                continue
            chains += 1
            closing = by_pair.get((first.root, first.first_contract, second.second_contract))
            if closing:
                full.add((first.symbol, second.symbol, closing.symbol))
            else:
                missing.add((first.symbol, second.symbol,
                             first.first_contract+second.second_contract+"@RTSX"))

    complete = sorted(full)
    incomplete = sorted(missing)
    return {
        "status": "STRUCTURAL_DISCOVERY_NO_BROKER_IDENTITY_PROOF",
        "input_assets": len(rows),
        "active_rtsx_native_spreads_parsed": len(instruments),
        "sequential_chains": chains,
        "complete_triple_patterns": len(complete),
        "missing_direct_patterns": len(incomplete),
        "complete": [{"first": a, "second": b, "direct": c,
                      "needs": "ACCOUNT_PARAMS_AND_SIGNED_CONTRACT_IDENTITY"}
                     for a,b,c in complete[:max_candidates]],
        "missing_direct": [{"first": a, "second": b, "query_symbol": c,
                            "exists_on_exchange": "UNKNOWN"}
                           for a,b,c in incomplete[:max_candidates]],
        "output_truncated": len(complete)>max_candidates or len(incomplete)>max_candidates,
        "rejections": dict(sorted(rejected.items())),
        "real_orders": 0, "realized_pnl": None,
        "warning": "Symbol-only leg decoding; validate actual exchange spread definition, multiplier, matching orientation, broker permissions, fees, books and atomic execution before pricing.",
    }