# NORM ARB V7 — trade-through evidence allocation, 17.10.2026

This is a **read-only correctness repair**, not an execution signal, trading
strategy promotion, or realized profit. No account/order APIs are used.

## Defects corrected in research logic

1. A public trade from NLMK or any other RTSX symbol must **never** be counted
   as evidence for a hypothetical WHEAT order.
2. A single public exchange trade ID cannot be counted repeatedly as several
   independent fills, either within repeated WebSocket frames or across multiple
   hypothetical orders evaluated in the same trading session.
3. A trade of size 1 must not support a hypothetical order of size 5. A
   partial-size candidate does not reserve any volume in the ledger.
4. Prints without a stable hashed exchange ID, symbol, trading-session ID,
   verified size units, timestamp AFTER latency, and strictly through the
   order price are never considered sufficient evidence.
5. The manual `finam_ws_collector.py` CLI now subscribes by default to
   available WHEAT **public INSTRUMENT_TRADES** in addition to books, rather
   than silently collecting only books. Override with `--trade-symbols`,
   or pass `--trade-symbols ""` to disable. The supervised service already
   supplied DEFAULT_TRADE_SYMBOLS and is unchanged.

## Integration contract

Call `conservative_trade_through(..., instrument_symbol='...@RTSX',
requested_volume='1', ledger=shared_ledger, volume_units_verified=True)`.
Build `shared_ledger=TradeAllocationLedger('YYYY-MM-DD:RTSX')` **once per
exchange trading day** and share it with every hypothetical order, including
across connections/replay windows. The caller must safely tag each flattened
market print with `symbol` and `session_id` from validated envelope context.
Do not provide `volume_units_verified=True` until broker/exchange quantity
semantics have been independently validated. A new ledger on every simulated
order invalidates the one-to-one allocation guarantee.

Even `TRADE_THROUGH_PROXY_NO_FILL_PROOF` is *not* a broker fill:
queue priority, cancellations, inventory valuation, live fee schedule,
markout, exchange-specific matching and tax/capital cost are unknown.
Therefore realized PnL=0, permission to trade=NONE, G1b account fees=UNKNOWN.

No running VPS process has been changed or deployed. These are GitHub
development-branch tests and source changes only; do not install on Forex VPS.
