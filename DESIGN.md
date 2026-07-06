# Seiche — Design Document

> A **seiche** is a standing wave in an enclosed body of water: invisible from the shore,
> until it sloshes over the edge. Funding stress behaves the same way. This tool detects
> the slosh before it crests.

**One-line pitch:** The opinionated funding-stress and leveraged-positioning terminal for
people who can't expense Bloomberg — money market plumbing + capital market fragility in
one pane, built entirely on free keyless public APIs.

**Date:** 2026-07-06 · **Status:** v1 build

---

## 1. Why this, why now (research verdict, 2026-07)

- **Regime:** QT ended Dec 1 2025. ON RRP ≈ $0–7B — the shock absorber is gone. Reserves
  ~$2.97T are testing the "ample" floor. Fed is doing Reserve Management Purchases
  (~$40B/mo bills). Fed funds 3.50–3.75%, hawkish hold. Every Treasury settlement now
  hits reserves directly.
- **Every 2026-era stress event was front-run by plumbing, not price:** Sep 15 2025
  (SOFR 4.51%, +18bp over EFFR), mid-Sep 2025 (SRF $18.5B), Oct 31 2025 (SRF $50B),
  Dec 31 2025 (SRF $74.6B record, repo 4.0%), Apr 2025 basis unwind, Mar 2026
  correlation shock. Screens looked calm until each break.
- **The RV complex migrated and tooling didn't:** basis trade ~$1–1.5T (sources disagree
  — itself a gap), swap-spread trade doubled to ~$631B. HF net repo borrowing ~$1.8T.
  The Fed is only "experimenting" with fusing futures OI × dealer repo. No vendor owns it.
- **White space (verified against incumbents + open source):** nobody fuses
  FRED + OFR STFM + NY Fed Markets + Treasury FiscalData + CFTC TFF into a
  forward-looking, alerting, provenance-honest instrument. Prior art is single-formula
  net-liquidity charts. Bloomberg has the data and no opinion at $32k/seat.

## 2. The seven engines (the inventions)

| # | Engine | Method | Output |
|---|--------|--------|--------|
| 1 | **Kink Engine** | Piecewise-linear (hockey-stick) fit of SOFR−IORB spread vs reserves; breakpoint = live estimate of the reserve-scarcity kink (NY Fed publishes this as occasional research; we compute it continuously). | Distance-to-kink in $B **and** in days at the trailing 20d drain rate. |
| 2 | **Liquidity Weather** | 6-week forward reserve path: reserves − forecast ΔTGA (auction settlement calendar + corporate tax dates + DTS seasonal flows) + RMP purchases. Bands from historical seasonal-forecast error quantiles. | Daily forward reserve curve + flagged **crunch windows** (quarter-ends, tax+settlement pile-ups) + kink-crossing date. |
| 3 | **Tail Seismograph** | NY Fed publishes the full SOFR/TGCR/BGCR distribution. Tail pressure = P99 − P50, z-scored vs trailing year. Tails widen days before the median moves (Sep 2025 proof). | Tail-pressure index + per-rate decomposition. |
| 4 | **Echo Engine** | Current 30-day trajectory of an 8-indicator z-score vector, similarity-matched (normalized distance) against a library of pre-stress windows before labeled episodes (2019-09-16, 2020-03-16, 2023-03-10, 2025-04-08, 2025-09-15, 2025-12-31). | "Today resembles T−8d before Dec-2025 squeeze, 0.72 similarity" + episode ranking. |
| 5 | **RV X-Ray** | CFTC TFF leveraged-fund UST futures shorts (notionalized per contract) × OFR DVP/sponsored repo volumes × NY Fed primary-dealer positions → transparent basis+swap-trade size proxy, reconciling the MS-$1.5T-vs-IMF-$1T dispute with a published method. Margin-shock simulator: DV01-based forced-unwind estimate for an X bp shock vs dealer absorption capacity. | Trade-size series, leverage proxy, unwind-amplification factor. |
| 6 | **Auction Digestion Index** | Every auction scored vs trailing same-tenor distribution: bid-to-cover z, dealer-takedown share z, indirect share z; EWMA into a cumulative supply-indigestion gauge; correlated forward into next-5d repo pressure. | Per-auction scorecards + digestion index. |
| 7 | **Seiche Index** | Weighted composite (0–100) of engines 1–6 sub-scores + level gauges (SOFR−IORB, SRF usage, RRP buffer). Regime classifier: CALM / EROSION / STRAIN / STRESS. Full decomposition — every point attributable. | The one number + why. |

**Cross-cutting principles (Tiktó-informed):**
- **Provenance-native:** every value carries source, series id, as-of date, fetch time, staleness class. No naked numbers.
- **Fail-loud:** a stale or missing feed renders as a visible fault, never silently drops out of a composite. Composite recomputes with published reduced-confidence flag.
- **Confidence-native:** forecasts ship with bands, composites with data-coverage %.

## 3. Data sources (all free, all keyless, all verified live 2026-07-06)

| Source | Transport | Series used |
|---|---|---|
| FRED (keyless CSV `fredgraph.csv?id=`) | daily/weekly CSV | WALCL, WRESBAL, WTREGEN, RRPONTSYD, IORB, EFFR, SOFR, BGCR, TGCR |
| NY Fed Markets API | JSON | `/api/rates/secured|unsecured/*` (with percentiles), `/api/rp/repo/*` (SRF ops), `/api/pd/*` (primary dealer) |
| OFR STFM API | JSON | `repo` dataset (DVP/GCF/tri-party rate+volume), `mmf` dataset (holdings by counterparty), `/calc/spread` |
| Treasury FiscalData | JSON | `operating_cash_balance` (daily TGA), `auctions_query`, `upcoming_auctions`, deposits/withdrawals |
| CFTC Socrata (`publicreporting.cftc.gov`) | JSON | TFF futures-only: leveraged-fund + asset-manager positions in 2Y/5Y/10Y/Ultra-10/Bond/Ultra-Bond |

Update cadences honored: SOFR ~8am ET, RRP 1:15pm, TGA ~4pm, H.4.1 Thu 4:30pm, PD stats Thu 4:15pm, COT Fri 3:30pm.

## 4. Architecture

```
seiche/
  backend/            Python 3.11+, FastAPI
    seiche/
      sources/        one client per upstream, uniform Series envelope w/ provenance
      store.py        SQLite cache (series obs + fetch log), TTL per cadence
      engines/        kink, weather, tails, echo, rvxray, auctions, composite
      api.py          REST: /api/overview /api/engines/* /api/series/*
      config.py       weights, thresholds, episode library  <-- tunable judgment lives here
  frontend/           Vite + React + TS + uPlot, dark terminal aesthetic
```

- No API keys anywhere. SQLite cache so cold-start is fast and upstreams aren't hammered.
- `config.py` holds the Seiche Index weights and thresholds — deliberately isolated:
  this is codified judgment, meant to be tuned by the operator, not buried in code.

## 5. Non-goals (v1)

- No paid data, no scraping behind logins. No intraday tick data (daily cadence + ops results is the honest granularity of the free stack).
- No auth/multi-tenant. Local-first side project; deployable later.
- Swap-spread levels (needs paid swap data) — RV X-Ray proxies the *funding leg* which is the fragility that matters.
