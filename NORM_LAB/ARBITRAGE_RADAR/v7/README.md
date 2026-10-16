# NORM ARBITRAGE RADAR — V7 streaming core

This directory belongs to the **existing** NORM ARBITRAGE RADAR project. It is not another trading product.

## Current gate (read-only)

`finam_ws_collector.py` is a **bounded, read-only evidence recorder** for Finam Trade API / RTSX native calendar spread order-book streaming. It intentionally contains **no order execution methods** and no modeled trading PnL.

- Verifies `readonly=true`, exactly one account, and non-delayed RTSX permissions before opening a socket.
- Waits for `HANDSHAKE_SUCCESS` before subscribing; subscribes to up to six symbols.
- Captures only `ORDER_BOOK` market-data payloads and whitelisted event metadata; excludes account information, errors' free-form text, raw tokens and order streams.
- Saves a new per-run JSONL file with SHA-256 digest; refuses to overwrite existing evidence.
- Hard-caps recorded evidence at 10,000 messages / 20 MB; returns a non-success status for truncated files, premature data or stream errors.
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

The module is prepared for integration but is **not yet wired to live
WebSocket frames or an authenticated secure server runtime**. All examples and
replay fixtures are synthetic; the private strategy and client data are
excluded from this public repository.

Current remote baseline: 49/49 deterministic offline tests passed in
[GitHub Actions run 38861921926](https://github.com/actforfoto-rgb/ETRN/actions/runs/38861921926).


### Offline triangle discrepancy gate — no VPS or trading credentials needed

`triangle_gate.py` checks **both algebraic directions** of an A–B + B–C = A–C native-calendar-spread triangle. It evaluates simultaneous BID/ASK and 3-order top-of-book depth with exact Decimal arithmetic, side-specific account rights, separately confirmed instrument identities and orientations, identical RUB price multipliers/lot rules, and three separate one-way broker + exchange + slippage + failed-hedge cost allowances. It refuses to infer missing tariffs or invent a third contract.

Any positive result is labeled **MODEL_ONLY_ATOMIC_EXECUTION_UNPROVEN**, not booked PnL, and depends on independently verified additivity/orientation. The six broker observations on 15 October contain an adjacent W4V6–W4X6 and W4X6–W4Z6 pair but **do not** contain the required direct W4V6–W4Z6 quote; therefore this evidence does not yield a confirmed triangle candidate. This is a structural research gap, not an instruction to trade or to assume the missing contract exists.

Offline entry point: import `scan_triangles` and supply quotes with explicit verified metadata and the broker's actual per-order cost fields. No external traffic, secret, balance or orders are needed for deterministic tests. The GitHub repository is PUBLIC; private account market tape and any proprietary strategy parameters must remain outside it.

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