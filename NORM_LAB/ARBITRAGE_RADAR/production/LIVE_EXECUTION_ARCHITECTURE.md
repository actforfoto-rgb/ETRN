# LIVE EXECUTION ARCHITECTURE

## State machine
IDLE -> PRECHECK -> LEG1_IOC -> LEG2_HEDGE -> HEDGED -> MONITOR -> EXIT_LEG1/EXIT_LEG2 -> CLOSED

Failure branches:
- LEG1 no fill -> ABORT.
- LEG1 partial -> size LEG2 to actual filled quantity.
- LEG2 partial / timeout -> emergency flatten LEG1 immediately.
- stale quotes / API error before first fill -> ABORT.
- stale quotes / API error after first fill -> EMERGENCY_HEDGE.
- venue truth != local state -> RECONCILE_MISMATCH -> freeze new entries.
- daily loss / exposure / stale-data threshold -> KILL_SWITCH.

## Production invariant
There must never be an intentional directional position. Any unhedged exposure is an execution incident with a strict maximum duration.

## Real-order enablement
Real orders require all three:
1. NORM_ARB_MODE=MICRO_LIVE or LIVE
2. NORM_ARB_LIVE_ACK=I_ACCEPT_REAL_ORDER_RISK
3. corresponding enable flag in strategy_registry.json

Default state fails closed.

## Credentials
Credentials are environment/secret-store only. Never commit API keys to repository files.

## Venue adapters
Crypto: CCXT private adapter can be used for MICRO_LIVE after venue-specific order/funding semantics are verified.
MOEX: broker-specific adapter is intentionally not selected yet. The execution engine interface is broker-neutral; selection happens after the winning MOEX mechanism is established and the user's broker/API access is known.
