# NORM ARBITRAGE RADAR — V7 streaming core

This directory belongs to the **existing** NORM ARBITRAGE RADAR project. It is not another trading product.

## Current gate (read-only)

`finam_ws_collector.py` is a **bounded, read-only evidence recorder** for Finam Trade API / RTSX native calendar spread order-book streaming. It intentionally contains **no order execution methods** and no modeled trading PnL.

- Verifies `readonly=true`, exactly one account, and non-delayed RTSX permissions before opening a socket.
- Waits for `HANDSHAKE_SUCCESS` before subscribing; subscribes to up to six symbols.
- Captures sanitized `ORDER_BOOK` plus explicitly allowlisted public `INSTRUMENT_TRADES` and whitelisted event metadata; excludes account information, errors' free-form text, raw tokens and private order/trade streams.
- Saves a new per-run JSONL file with SHA-256 digest; refuses to overwrite existing evidence.
- Hard-caps recorded evidence at 10,000 messages / 12 MB; returns a non-success status for truncated files, premature data or stream errors.
- Rejects non-integral exchange envelope timestamps; unrecognized market-data fields and account payloads never enter the evidence log.
- The saved messages must be independently reconciled to REST bootstrap snapshots before interpreting book updates as executable top-of-book.
- No real broker stream has yet been verified **for this V7 module**. Unit tests use synthetic messages; do not confuse test PASS with access or profit.

### Book reconstruction and current CI gate

`book_replay.py` provides a deterministic, Decimal-based REST snapshot + explicit
ORDER_BOOK WS add/update/remove replay. It rejects ambiguous/partial updates,
crossed books, out-of-order timestamps and updates arriving before bootstrap.
A connection interruption invalidates the book until a new REST snapshot is
obtained. The normalized view **always** reports continuity_verified=false and
executable=false; a best BID/ASK never means broker FOK would fill.

The local `capture_stream` calls `BookReplay` for sanitized WS frames, but this
public GitHub code and its offline fixtures are **not proof of a secure continuous
live stream**. Account-specific tape remains private, outside public GitHub.

Current verification is recorded by the latest GitHub Actions run for branch
`norm-arb-v7-dev`; only its verified CI status, never test count alone,
should be treated as code QA, not proof of market profitability.


### Offline triangle discrepancy gate — no VPS or trading credentials needed

`triangle_gate.py` checks **both algebraic directions** of an A–B + B–C = A–C native-calendar-spread triangle. It evaluates simultaneous BID/ASK and 3-order top-of-book depth with exact Decimal arithmetic, side-specific account rights, separately confirmed instrument identities and orientations, identical RUB price multipliers/lot rules, and three separate one-way broker + exchange + slippage + failed-hedge cost allowances. It refuses to infer missing tariffs or invent a third contract.

Any positive result is labeled **MODEL_ONLY_ATOMIC_EXECUTION_UNPROVEN**, not booked PnL, and depends on independently verified additivity/orientation. The six broker observations on 15 October contain an adjacent W4V6–W4X6 and W4X6–W4Z6 pair but **do not** contain the required direct W4V6–W4Z6 quote; therefore this evidence does not yield a confirmed triangle candidate. This is a structural research gap, not an instruction to trade or to assume the missing contract exists.

Offline entry point: import `scan_triangles` and supply quotes with explicit verified metadata and the broker's actual per-order cost fields. No external traffic, secret, balance or orders are needed for deterministic tests. The GitHub repository is PUBLIC; private account market tape and any proprietary strategy parameters must remain outside it.


### Native calendar spread versus both outright futures — closer to venue mechanics

The Moscow Exchange officially describes **synthetic matching**: a native
calendar spread may match liquidity from the separate books of its two
constituent futures. Source: https://www.moex.com/ru/spreads

`spread_leg_parity.py` evaluates both illustrative parity directions:
BUY native calendar vs SELL synthetic (SELL far future / BUY near future),
and SELL native vs BUY synthetic (BUY far / SELL near). It checks three
simultaneous bid/ask books, same monetary point value, verified constituent
identities and sign, single-contract lot ratios, permissions, depth and
three complete sets of declared one-way fees/slippage/failure reserves.
**It is not a proven arbitrage**, and synthetic matching may already remove
precisely these discrepancies inside the exchange. All modeled positives
are labeled non-atomic/non-executable pending live confirmation. It contains
no brokerage execution or real account information; 16 offline tests cover
both directional formulas and fail-closed guards.

### Fast development loop

Every push to branch `norm-arb-v7-dev` that changes these V7 files runs the `NORM ARB V7 CI` GitHub Actions test workflow. This does **not** consume Floot's 100-per-day action quota. It is not a permanent trading daemon.

```sh
cd NORM_LAB/ARBITRAGE_RADAR/v7
python -m unittest discover -s tests -v
```

### Bounded live observation (only after secure deployment)

Requirements: Python 3.12+, `websockets>=14,<17`, and an existing Finam read-only secret stored exclusively as a protected environment variable on a trusted runtime. Never place API secrets in code, GitHub commits, GitHub issues, workflow YAML, screenshots or chat messages. The recorder never embeds or prints the read-only secret.

```sh
python -m pip install "websockets>=14,<17"
python finam_ws_collector.py --seconds 90 --output-dir evidence
```

The collector fails closed if `FINAM_READONLY_API_KEY` is absent. It does not automatically run on GitHub Actions, which lacks the Floot-held secret. A 24/7 runtime is still an outstanding infrastructure task; the Forex production VPS is reserved for Forex, not this research process.

### Next engineering gates

1. Prove a live WS subscription and persist *actual* market updates in a suitable secure runtime.
2. Reconcile first REST snapshot with stream updates, including add/update/remove, sequence/time checks and reconnect behavior.
3. Scan a dynamic approved native-spread universe, not a fixed six forever.
4. Model full entry **and exit** after account-specific margin, lot, broker tariff, exchange fees and execution restrictions.
5. Only after independent positive net cycles and explicit authorization consider a separate trading adapter.

**Real orders: 0. Realized PnL: 0. LIVE trading: disabled.**

### Full causal event replay (17 October 2026)

\`shadow_replay.py\` is the first **OFFLINE ONLY** market-event simulator for the
existing V7. It consumes a bounded *already captured* sanitized Finam JSONL tape,
reconstructs books, conditionally places passive entry and exit orders in causal
order, models latency, visible queue-ahead, unique public trade IDs, partial/open
risk, and forced-taker-BBO-only scenarios. It NEVER calls broker trading APIs.
Its default gating **rejects conditional fills** until public-trade units and
aggressor-side semantics are explicitly verified. A real-time book/print or
positive *conditional* net is NOT a broker execution. All outputs keep
\`realized_pnl_rub=null\`, \`actual_orders=0\`, \`profitability_proven=false\`.

The user-approved methodological correction and strict acceptance protocol
are documented at:
\`research/SHADOW_EVENT_REPLAY_METHOD_20261017.md\`.

Regression suite: \`tests/test_shadow_replay.py\` (synthetic market data only).
A true strategy-level economic verdict still REQUIRES a future, continuous,
account-eligible, fee-verified recording and conservative follow-through on
unclosed positions. Never copy private account tapes into this PUBLIC repo.


#### Fail-closed safety follow-up

The official Finam public-trade protobuf has a per-trade
\`is_data_snapshot\` field. The sanitized collector now preserves it;
the replay ignores historical snapshot prints and rejects legacy tapes
that had lost the bit. The documented buy/sell \`Trade.side\` is **not**
by itself evidence of taker/aggressor direction, and public trade size
is **not** automatically identical to spread-order lots. Those gates
remain OFF until separately verified. Cancellation without ACK,
mid-stream resnapshots and forced best-bid-only exit never free
hypothetical capital. See method documentation for exact limitations.
