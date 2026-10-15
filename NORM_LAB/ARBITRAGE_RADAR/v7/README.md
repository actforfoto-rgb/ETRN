# NORM ARBITRAGE RADAR — V7 streaming core

This directory belongs to the **existing** NORM ARBITRAGE RADAR project. It is not another trading product.

## Current gate (read-only)

`finam_ws_collector.py` is a **bounded, read-only evidence recorder** for Finam Trade API / RTSX native calendar spread order-book streaming. It intentionally contains **no order execution methods** and no modeled trading PnL.

- Verifies `readonly=true`, exactly one account, and non-delayed RTSX permissions before opening a socket.
- Waits for `HANDSHAKE_SUCCESS` before subscribing; subscribes to up to six symbols.
- Captures only `ORDER_BOOK` market-data payloads and whitelisted event metadata; excludes account information, errors' free-form text, raw tokens and order streams.
- Saves a new per-run JSONL file with SHA-256 digest; refuses to overwrite existing evidence.
- The saved messages must be independently reconciled to REST bootstrap snapshots before interpreting book updates as executable top-of-book.
- No real broker stream has yet been verified **for this V7 module**. Unit tests use synthetic messages; do not confuse test PASS with access or profit.

### Fast development loop

Every push to branch `norm-arb-v7-dev` that changes these V7 files runs the `NORM ARB V7 CI` GitHub Actions test workflow. This does **not** consume Floot's 100-per-day action quota. It is not a permanent trading daemon.

```sh
cd NORM_LAB/ARBITRAGE_RADAR/v7
python -m unittest discover -s tests -v
```

### Bounded live observation (only after secure deployment)

Requirements: Python 3.12+, `websockets>=14,<17`, and a **NEW** Finam read-only secret stored exclusively as a protected environment variable on a trusted runtime. Never place API secrets in code, GitHub commits, GitHub issues, workflow YAML, screenshots or chat messages. Do not reuse any key previously posted in chat.

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