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

---

# v2 "Deep Water" addendum — 2026-07-07

## What v2 adds (design intent)

v1 was a monitor: it told you stress was building. v2 is an instrument: it tells you
what to look at, what it predicts, and — critically — how good its own predictions
have been. Three design moves:

**1. Novel structural engines (fragility invisible in levels).**
- *Resonance Engine* — the seiche metaphor made mathematical. Calendar forcings
  (month-end, quarter-end, year-end, mid-month settlements, tax dates — disjoint
  modes) recur with near-identical size; the market's RESPONSE to them is the
  measurable. Slosh amplitude per event, amplification trend (recent vs prior
  median), post-event decay half-life. A basin that rings louder and longer to the
  same bell is losing damping. Live finding at build time: year-end amplification
  6.3×, quarter-end 2.6× with decay 1.0d → 2.5d.
- *Hydrophone Array* — Kritzman absorption ratio applied to funding plumbing (11
  standardized daily-change series) + a lead-lag map (max lagged cross-correlation)
  of which pipe is upstream. Densifying network = shocks transmit, not absorb.
- *Warehouse* — NY Fed primary-dealer positions by maturity bucket; saturation
  percentile. Live finding: dealer net UST inventory at its 99th percentile,
  11–21y bucket at its 100th.
- *Global Basin Coupling* — same physics across basins: US (SOFR−IORB), euro area
  (€STR−DFR via ECB Data Portal), UK (SONIA), channels (broad dollar, foreign
  official RRP, H.4.1 swap lines). The Tide = common-component share; swap-line
  draws ex small-value tests = the global confession channel. Out-of-scope basins
  (Japan/China/Russia/Africa: no keyless daily feed meeting the provenance bar)
  are declared, not faked.

**2. The money layer (what a user does with it).**
- *The Tell* = plumbing percentile − market-priced-stress percentile (VIX, HY/IG
  OAS, 10y realized vol; official FRED series only — Yahoo/Stooq refused bots and
  were dropped for provenance). Positive Tell = hedges are cheap relative to what
  the plumbing knows.
- *Turn Barometer* = amplitude forecast for the next known-date turn, LOO-CV'd,
  benchmarked vs naive, self-demoting when it can't beat naive.
- *Playbook* = state-conditioned forward outcome tables in native units with n and
  overlap disclosed.

**3. The honesty layer (what makes it pitchable).**
- *PROOF* — Seiche-lite index rebuilt under expanding-window statistics only; the
  no-look-ahead property is enforced by a unit test (value at T must be identical
  when future data is appended). Recall/precision vs base rate, episode lead
  times including the misses (Mar-2020 and Apr-2025 were exogenous, not plumbing —
  an honest funding gauge should not claim them).
- *Time Machine* — /api/asof/{date}: every engine is a pure function of its input
  series, so truncation replays the historical board faithfully (final-vintage
  caveat printed). Sep-12-2019 replay flags Sep-16 as a crunch window.
- *PIT record* — every live snapshot appends the as-published index to `pit:*`;
  from v2 onward the tool accrues an untouchable forward track record.

## v2 data additions (all verified live 2026-07-07)

| Source | New pulls |
|---|---|
| FRED | VIXCLS, HY/IG OAS, DGS2/10/30, DTB3/DTB4WK, SP500, NFCI, WLCFLPCL (discount window), ECBDFR, IUDSOIA (SONIA), SWPT (swap lines), DTWEXBGS (broad dollar), WLRRAFOIAL (foreign official RRP) |
| NY Fed | primary-dealer positions (`/pd/get/{keyid}.json`, spliced weekly history), USD FX-swap operations (`/fxs/usdollar/last/N.json`, small-value test ops flagged) |
| ECB Data Portal | €STR daily (`EST/B.EU000A2X2A25.WT`, csvdata) |
| CFTC TFF | + FED FUNDS, SOFR-1M, SOFR-3M, E-MINI S&P 500 (crowding panel) |
| FiscalData | TGA extended to 2019 (pre-2021 label "Federal Reserve Account"), auctions to 2018, upcoming auctions wired into Weather as settlement calendar |

Composite v2 weights (config.py): tails .18, kink .14, weather .12, confession .12
(SRF + discount window, max), rvxray .12, resonance .10, hydrophone .08,
auctions .06, warehouse .04, buffers .04. Echo, Tell and Basin Coupling are
reported alongside, never weighted in (context/signal, not stress evidence).

## Also considered and rejected

- Yahoo Finance / Stooq ETF prices — both block or throttle non-browser clients;
  a flaky feed can't sit under a provenance-honest instrument. Market leg uses
  FRED official series; playbook outcomes speak native units instead of ETF PnL.
- Weighting Echo or The Tell into the composite — resemblance and divergence are
  not stress evidence; they stay context.
- Faking global coverage (Japan/China/Russia/Africa) from monthly or scraped
  data — declared out of scope instead; basins plug into config when a
  qualifying feed exists.

---

# v2.1 addendum — crypto, India, ML Lab, desk assistant (2026-07-07)

**Gap audit verdict that drove this round:** v2 had no offshore-dollar coverage
(stablecoins ARE money market funds now — $200B+ of T-bills — and crypto is the only
dollar market open on weekends), no India coverage (home turf), no learned layer over
the honest feature history, and no assistant to read the board aloud.

- **Stablecoin Moorings** — DeFiLlama peg board + ~8y total-circulation history,
  Coinbase USDT-USD daily closes as the peg-history series, BTC 10d realized vol +
  largest-weekend-move canary. Context engine: never weighted into the composite.
  Small-value swap-test discipline carried over: USDC/DAI pegs are spot-only and say so.
- **India basin** — CCIL (HTML-only) and RBI DBIE (broken SSL chain) fail the keyless
  bar, both probed 2026-07-07. India joins through the FX channel (FRED DEXINUS daily,
  official): level z + realized-vol z + a coupling row in the Tide panel. Declared
  partial, not faked; a rates anchor (WACR/MIBOR) plugs in when a qualifying feed exists.
- **ML Lab** (`engines/mlpred.py`, sklearn HistGradientBoosting) — P(funding event
  within 5bd), same event definition as PROOF. 22 trailing-only features (plumbing,
  market, crypto, calendar distances). Walk-forward refits every 42bd after 500d
  warmup; NO shuffled CV. Benchmarked against climatology AND the rule-based index;
  the verdict self-demotes when it loses. Build-time result: OOS AUROC 0.813 vs
  0.806 rule-based, Brier beats climatology; reliability table shows top-bin
  overconfidence (printed, not hidden). Top feature: bd_to_month_end — the calendar
  dominates, independently confirming the Resonance thesis. Known trap fixed: an
  all-NaN column (crypto pre-2021) crashes the HGB binner — columns drop per-fit
  until they have data.
- **Desk assistant** (`ai.py`) — deterministic context pack (composite decomposition,
  headline, Tell/Turn/ML, calendar, movers, faults, staleness counts) is the model's
  ONLY world; system prompt requires engine+asof citations and "not on the board"
  over improvisation. Routed via free-llm-router free tiers (fast tier preferred —
  free smart tiers serve reasoning models that leak chain-of-thought; a stripper
  handles the leak when smart is the only survivor), else SEICHE_LLM_* env endpoint,
  else fails open returning the pack itself. Surfaces: /api/ask, `seiche ask`, BOARD.
