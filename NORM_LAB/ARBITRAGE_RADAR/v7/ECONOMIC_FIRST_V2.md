# NORM ARBITRAGE RADAR — Economic-First V2

**Research-only operating contract, 2026-10-18.** This is a process specification, NOT proof of profitable trading. Existing V7 software, tests and historical evidence remain unchanged. Do not create a new app or fork Forex/MT5 production.

## Target

Discover **event-driven, executable, after-cost** opportunities across a broad instrument universe. A fixed pair is never the project. Prove the economic edge *before* investing in production execution code. No promised monthly return. Real orders are disabled.

## Six engines (independent hypotheses)

| ID | Class | Primary proof to seek |
|---|---|---|
| M1 | Native calendar-spread dislocations | Fair spread vs executable native order, full exit, account fees/collateral |
| M2 | Cross-venue perpetual funding + basis | Actual settlement schedules, two-leg book execution, basis exit, liquidation stress |
| M3 | Options synthetic financing and native combos | 4-leg atomicity where available, payoff, premiums/fees, expiry and assignment |
| M4 | Pre-funded cross-venue spot/perp | Prefunded inventory, simultaneous depth, inventory rebalancing and financing |
| M5 | Single-chain atomic DEX paths | Per-block state simulation, gas/MEV/flash-loan premium, inclusion and revert risk |
| M6 | Passive market-making and rebates | Queue, adverse selection, trade-through, maker/taker costs, inventory and forced-taker exit |

M6 is NOT risk-free arbitrage. It must be audited and ranked independently even when M1 and M6 share market-data infrastructure.

## Gate vocabulary

IDEA → DATA_READY → ECONOMIC_SCREEN → OOS_POSITIVE → FORWARD_EXECUTABLE_SHADOW → PAPER_ELIGIBLE → (separately authorized) LIVE_ELIGIBLE.

* **DATA_BLOCKED:** no trustworthy route/time alignment/access; never call this a negative economic result.
* **ECONOMICALLY_REJECTED:** negative net on a specified venue/window/fee/capital scenario; never infer arbitrage as a whole is impossible.
* **NOT_PROVEN:** inconclusive evidence; do not turn this into a claim of profit.
* **PROMISING_OOS:** independent historical after-cost evidence, not executable/live profit.
* **SHADOW_CANDIDATE:** causally collectable executable books and costs; readiness only.
* **PAPER_ELIGIBLE:** preliminary multi-day closed shadow proof; still not LIVE authorization.

## One opportunity record (minimal evidence contract)

Each independent opportunity must identify: mechanism, venue and exact instrument identity including multiplier, signal trigger frozen before replay, exchange timestamp and receive timestamp, raw source hash, quote/orderbook validity, bid/ask and size, side and quantity per leg, maker/taker/native order semantics, latency, fill/no-fill/partial-fill and liquidation/hedge contingencies, genuine or conservative closing bid/ask, fees, funding, financing, margin, inventory/borrow/transfer costs, capital locked, completed net P&L and stress scenarios. Preserve discarded/negative events and missing fills: no cherry-picking.

Never use an asynchronous snapshot as a simultaneous cross-market executable trade; never mark last/mark as a real tradable price; never treat a quote touch as an actual passive fill; never annualize a brief gross mismatch into guaranteed profit.

For frequent mechanisms, a **preliminary** shadow milestone is ≥30 independent closed executable virtual cycles across ≥3 trading days, positive aggregate and median net, positive fraction ≥55%, robust to fees/adverse selection, and without one lucky event dominating. This milestone alone does not establish statistical certainty. For slow carry/expiry strategies preregister an appropriately longer independent horizon; do not split one carry into dozens of fictitious trades.

## Economic KPIs only

Priority: event count per market day; independently fillable events; after-cost net per full closed cycle; absolute total-capital net, capital occupancy and rebalancing cost; worst trade/max drawdown; distribution of adverse moves; capacity/queue, fill probability; comparative after-cost return vs passive cash alternative; OOS/forward uncertainty and sensitivity.

Unit test PASS, file hashes, ZIP count, catalogue size, CPU activity or a new release number are **engineering checks**, never economic outcomes.

## One existing stack

Existing GitHub dev branch, Google Drive canonical evidence, and existing Floot market-data app. Safe read-only research only. Do not modify any production trading robot, VPS, or existing research tapes. No additional compute/server expense without owner's explicit consent.

No API credentials, broker/account data, raw private tape, unpublished trading signal thresholds or user-specific strategy details in this public repository. Store such material in the authorized private environment only. Respect exchange/broker geography and ToS restrictions; do not evade them.

## Autonomous work discipline

On each scheduled execution, read the latest canonical evidence and this document; choose ONE highest-information **economic** experiment that can be safely completed without user intervention. Retain coverage of all six mechanism classes and do not repeatedly focus on a single pair. Capture a reproducible dataset/ledger and record a clear decision; switch classes when the hypothesis fails. A tool/venue data limitation is not permission to stop every other class.

Do not autonomously place real/paper/micro orders, spend money, publish secrets, change user systems, install on FX VPS, or claim unattended 24/7 market collection without evidence of an actual persistent collector. The assistant may work on its hourly scheduled run, not continuously between runs.

**Success phase 1:** at least one causally supported positive after-cost executable full-cycle mechanism on an independent period and available permitted venue. **Success phase 2:** after explicit owner permission, separately compare actual micro fills to shadow predictions before any production deployment.
