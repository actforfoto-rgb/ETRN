# NORM ARBITRAGE BOT — PRODUCTION CONTRACT

## Endpoint
Final target: a fully automated two-leg arbitrage/funding execution system that produces positive **realized NET P&L after all costs** on real capital. Profit cannot be guaranteed; promotion to real capital is evidence-gated.

## Stages
1. RESEARCH — prove mechanism on historical/current market data.
2. SHADOW — autonomous live signals, no orders; exact executable bid/ask and funding ledger.
3. PAPER — persistent simulated positions with actual funding events and exit basis.
4. MICRO-LIVE — smallest practical capital; real fills and fees; strict loss/leg-risk limits.
5. LIVE — controlled scaling only after realized evidence.

## Mandatory promotion gates
No stage is skipped.

### RESEARCH -> SHADOW
- reproducible positive NET on a holdout period;
- all fee schedules verified;
- no look-ahead;
- bid/ask execution, not mid;
- funding/SwapRate timing modeled correctly.

### SHADOW -> PAPER
- minimum 30 candidate cycles;
- positive median NET;
- positive aggregate NET;
- no dependency on one isolated outlier;
- leg-risk and data outages recorded.

### PAPER -> MICRO-LIVE
- minimum 30 closed paper cycles OR 30 calendar days, whichever is longer;
- aggregate NET > 0 after conservative fees and execution buffer;
- p05 cycle loss inside configured risk budget;
- zero unhedged state longer than the emergency threshold;
- kill-switch and reconciliation tested.

### MICRO-LIVE -> LIVE
- real exchange/broker fills match the model closely enough;
- actual fees/funding reconcile;
- no operational incidents;
- positive realized NET after a minimum evidence window set from observed trade frequency.

## Live engine requirements
- two-leg atomic/semi-atomic execution;
- fill-or-hedge state machine;
- partial-fill recovery;
- max unhedged notional and max unhedged seconds;
- venue balance/margin checks before entry;
- funding calendar and contract-expiry calendar;
- order-idempotency;
- position reconciliation against venue truth;
- persistent state after restart;
- per-route fee model from actual account tier;
- per-venue API rate limits;
- max daily loss;
- max venue exposure;
- max strategy exposure;
- circuit breaker on stale data / API divergence / abnormal spread;
- explicit PAPER / MICRO_LIVE / LIVE modes.

## Current priority mechanisms
A. Crypto cross-exchange perpetual funding/basis:
- OP and ARB routes across OKX / HTX / MEXC / Gate / Bitget.
- Signal requires expected funding + executable cross-basis to clear full round-trip costs and execution buffer.

B. MOEX perpetual-vs-quarterly:
- CNYRUBF ↔ CRZ6
- USDRUBF ↔ SiZ6
- IMOEXF ↔ MMZ6
- RGBIF ↔ RBZ6
- EURRUBF ↔ EuZ6
Current screens are conditional on future SwapRate persistence and must pass historical validation.

## Live-capital rule
The system must never interpret a positive screen as permission to trade real money. Real order routing is enabled only after the stage gate is explicitly promoted and credentials are supplied for the selected venue/broker.


## Low-frequency MOEX curve exception
Quarterly perpetual-vs-fixed strategies cannot reasonably satisfy a 30-closed-cycle gate without waiting many years. For these strategies the evidence gate is frequency-adjusted:

SHADOW -> PAPER:
- at least 7 completed historical expiry cycles where available;
- a chronologically separate holdout with positive aggregate NET;
- conservative stress aggregate NET positive;
- at least 20 live STRONG scans over at least 5 calendar days.

PAPER -> MICRO-LIVE:
- at least 30 calendar days of uninterrupted paper/shadow operation;
- no reconciliation or data-integrity incident;
- current signal must still satisfy the research-approved entry rule;
- cumulative paper mark/funding economics remain non-negative after conservative execution costs;
- MICRO-LIVE is one smallest practical pair only, with a hard kill-switch.

This exception does not weaken the real-money safety controls. It changes only the sample-count requirement to match quarterly trade frequency.
