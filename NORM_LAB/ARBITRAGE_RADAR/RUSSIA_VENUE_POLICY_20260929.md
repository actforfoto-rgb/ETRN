# NORM ARBITRAGE RADAR — venue eligibility filter for a Russia-based user
Snapshot: 2026-09-29

This file separates TWO different questions:
1. Can the cloud research runner technically reach a public market-data API?
2. Do the exchange's currently published terms list the Russian Federation as a prohibited/restricted jurisdiction?

These are not the same thing. A GitHub runner receiving 403/451 from an exchange is a property of the runner's network/geography, not evidence that a Russia-based user is prohibited.

## Published-terms screen

### Bybit — CANDIDATE
Current Bybit restricted-country page does NOT list the Russian Federation as an excluded jurisdiction. It does list specified Russian-controlled regions of Ukraine and sanctions-listed persons/entities.
Research cloud runner: blocked by CloudFront geography.
Meaning: keep Bybit in the LIVE-candidate universe; use another collection route for market data.

Official source:
https://www.bybit.com/en/help-center/article/Service-Restricted-Countries

### Bitget — CANDIDATE WITH TRANSFER COMPLIANCE NOTE
Current Bitget Terms do NOT list the Russian Federation as a prohibited country. Sanctioned persons/entities remain restricted.
Important operational note: Bitget announced enhanced controls on transfers involving several named entities, including HTX, effective Aug 2026. Therefore a Bitget↔HTX cross-venue strategy must NOT assume frictionless direct transfers/rebalancing.
Official sources:
https://www.bitget.com/support/articles/360014944032-terms-of-use/
https://www.bitget.com/support/articles/12560603892077

### HTX — CANDIDATE
Published HTX restricted-jurisdiction list does not list the Russian Federation as an all-services or derivatives-prohibited jurisdiction. Sanctions/export-control clauses still apply.
Official source:
https://www.htx.com/support/ru-ru/detail/360000112261

### MEXC — CANDIDATE
Current MEXC restricted-country list does not list the Russian Federation itself; it lists specified Russian-controlled regions of Ukraine and sanctioned jurisdictions/persons.
API futures fees are separate from app/web fees and must be modeled using the API schedule for an automated strategy.
Official sources:
https://www.mexc.com/terms
https://www.mexc.com/announcements/article/updates-to-api-futures-trading-fees-jun-1-2026-17827791535742

### OKX — CONDITIONAL CANDIDATE
OKX's current risk/compliance disclosure lists Russia under restrictions for fiat payments, not as a blanket unsupported region in the cited disclosure. Product-level availability still needs account-specific verification before LIVE use.
Official source:
https://www.okx.com/ru/help/risk-compliance-disclosure

### Binance — VERIFY BEFORE LIVE
Do not infer Russia eligibility from the GitHub runner's 451 response. The runner's location is not the user's location. Binance terms state that services may be restricted by jurisdiction and prohibit circumventing restrictions. Account/product availability for a Russia-resident user must be verified directly in the current Binance account/terms before treating it as a LIVE venue.
Official source:
https://www.binance.com/ru/terms

### Kraken / Gate — RESEARCH ONLY UNTIL ELIGIBILITY CHECKED
Cloud technical reachability alone is not enough for LIVE candidacy. Keep for research price/funding comparisons, but do not plan execution until current Russia-resident eligibility and product access are verified.

## Current venue priority for ARB-C02
Tier A research+potential execution candidate: Bybit, MEXC, HTX, Bitget.
Tier B conditional: OKX.
Tier C research/verification: Binance, Kraken, Gate.

No VPN/geolocation bypass is part of NORM ARBITRAGE RADAR.
