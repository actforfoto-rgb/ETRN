# NORM ARBITRAGE RADAR V7 — Offline acceptance notes

This source package is **not an executable trading robot** and does not contain
any broker order execution APIs. The existing Windows VPS production process
was neither touched nor modified when this release was prepared.

## Verified root cause

Real 40-second VPS diagnostic on 2026-10-16 collected 272 ORDER_BOOK plus
412 QUOTES market DATA frames. Finam's envelope payload is a JSON string,
with protobuf JSON field names commonly camelCase. The old collector filtered
those messages out as unrecognized, and falsely counted 98 empty rounds
as successful because `subscribed=true` only meant SUBSCRIBE had been sent.

## Changes

- JSON-string and ProtoJSON snake/camel input normalization.
- Oneof buy/sell order-book snapshot/update/remove; no deletion of omitted side.
- Reconnections and invalid deltas invalidate reconstructed book state;
  REST/bootstrap and WS snapshots distinguished explicitly.
- Instrument-trades read-only subscription on two WHEAT calendars;
  public prints are never confused with personal broker TRADES.
- No skipping malformed order-book deltas while retaining continuity.
- Realized PnL always unknown/none, never set to a synthetic outcome.
- Economic upper bound requires actual account-specific fee evidence and
  margin in RUB, quote point value, full execution buffer, and depth.
- Fail-closed TLS verification through Windows trust store.
- No synthetic backfill of absent prices; no maker fill assumed from a touch.
- Explicit completed and empty cycles counters; cumulative evidence retention.
- Source SHA256 integrity manifest to catch incomplete or modified packages.

## Acceptance matrix

| Gate | Evidence | Status |
|---|---|---|
| Testable Finam WS data source | 684 live DATA frames from VPS pre-fix | PASS transport |
| Auth / TLS | FINAM_READONLY_VERIFIED, TLS_CERTIFICATE_VERIFIED_NATIVE_WINDOWS | PASS historical |
| New streaming parser | synthetic ProtoJSON / WS mock | OFFLINE PASS |
| Live new parser on VPS | not deployed | NOT CHECKED |
| Live full WS snapshot and replay continuity | not seen with new version | NOT CHECKED |
| Market-trade print feed for WHEAT | subscribed only in new code | NOT CHECKED |
| Fees / margin / point RUB on this account | no documentary schedule | OPEN |
| Market making hypothetical net closed cycles | none validated | OPEN |
| Broker native order capability/commission | not verified | OPEN |
| Actual closed profitable trading | none | ZERO |
| User authorized real orders | no | NO |

**The only permissible label for any calculated positive is MODEL UPPER BOUND,
not trade PnL.**