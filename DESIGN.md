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

---

# v2.2 addendum — the orthogonal test + gap closures (2026-07-07)

Driven by an honest self-review. Every criticism got a fix or a declaration:

- **Orthogonal signal test (the centerpiece).** Criticism: the lite index contains
  spread/tail terms and the event IS a spread spike — partial self-prediction.
  Fix: rebuild the index with `exclude=("tails",)` (leaving kink-proxy, confession,
  rvxray, auctions, buffers — none derived from the target variable) and rerun
  event capture. Result: recall 0.692 [Wilson 0.42–0.87] vs 0.615 full signal;
  run-precision ~2× base rate; orthogonal signal at 98th–100th pctl with 42d leads
  before Sep/Dec-2025 and 84th/34d before SVB. Claim upgraded from "correlated
  gauge" to "structural leading signal". Same treatment for ML: dropping the whole
  spread family moves OOS AUROC only 0.826 → 0.812.
- **Small-n honesty.** Wilson 95% intervals on every backtest rate; RUN-level
  precision (2/20 runs) is the headline because alert days are serially
  correlated; resonance publishes `amplification_ex_max` (largest recent slosh
  removed) + low-n flags; playbook cells with <8 independent windows render dimmed.
- **Boundary-label leakage in ML.** Training rows whose 5bd-forward labels see
  into the test block are now embargoed. Consequence, published: the earlier
  Brier "win" vs climatology was partly leakage — embargoed Brier 0.0402 vs
  0.0388 climatology. Verdict split into ranking (beats rule, 0.826 vs 0.806)
  vs calibration (fails — "use for ranking/alerting, not literal odds").
- **Decision-utility scoring** (Jane Street competition lineage): +1 per caught
  event, −0.25 per false alarm, per year. ML@25% = +1.2/yr, orthogonal ML = +1.5/yr,
  rule@80th = −9.2/yr — the rule index is a REGIME gauge, the model is the action
  filter. Reframing surfaced by the metric itself.
- **Turn Barometer self-demotion hardened**: model must beat naive by >0.05 LOO
  skill to take the headline; both forecasts always published.
- **Deep-cache failure poisoning fixed**: a blob computed with any failed layer
  lives 30 minutes, not 12 hours.
- **CI gate**: publish workflow now runs the 25-test suite before exporting.
- **Station-Keeping** (satellite-repo method transfer): propagate expected
  TGA/RRP/WALCL state, CUSUM the innovations, alarm on unmodeled burns (caught
  the +$294B April-2026 TGA build). GNSS-interference canary (gpsjam) probed and
  REJECTED — data endpoint not publicly fetchable; declared, not scraped.
- **PIT record now running**: launchd agent loaded (08:15/16:45 daily) — the
  as-published track record accrues from today.

---

# v2.3 addendum — the forecast layer (2026-07-07)

Built in tandem across two Claude Code sessions: session A shipped **Tide
Tables** (analog forecasting, PR #1) with a pattern-first roadmap
(docs/IDEAS.md); session B built on top of that branch, shipping the roadmap's
highest-ranked items plus the piece neither list had. Design intent: v2 told
you how stressed the basin is TODAY; v2.3 tells you WHEN — with every forecast
carrying its own walk-forward evidence.

**1. Undertow — the damping gauge (critical slowing down).** Systems
approaching a regime shift relax more slowly (Scheffer et al., Nature 2009 —
the literature is literally about lakes). Rolling lag-1 autocorrelation +
variance of the rolling-median-detrended SOFR−IORB and tail series, an
implied relaxation time τ = −1/ln(AC1), and the unconditional version of
Resonance's decay measurement: median recovery half-life after every pop
above the expanding 90th percentile, trailing year vs prior. Completes the
physics pair — Resonance = forced response to the calendar bell, Undertow =
free decay on ordinary days. Expanding percentiles only (truncation-equality
unit test); joins the composite at 0.06 as structural-fragility evidence
(weights rebalanced: tails .17, kink .13, weather .11, rvxray .11,
warehouse/buffers .03 each). EWS pitfall handled: indicator series are
serially correlated, so Kendall-tau p-values are anti-conservative — the
engine publishes percentiles vs own history, never trend significance.

**2. Swell Forecast — the funding-stress forward curve (the new invention).**
Nobody — not Bloomberg, not OFR — publishes a term structure of funding
stress, yet the basin's forcing schedule is public. For each of the next 42
business days: P(pop ≥ 2/5/10/20bp), where pop = SOFR−IORB minus trailing
5bd median (the exact PROOF event statistic). Estimator learned from a failed
spike: per-cell empirical hazards starve on rare buckets (~4 quarter-ends a
year), so each bucket keeps its FULL expanding distribution of pops and every
severity reads off the same exceedance curve — small sloshes lend the big
ones statistical mass (AUROC 0.53 → 0.88 on the synthetic testbed). Disjoint
buckets: year/quarter/month turn (spanning the boundary), tax date,
mid-month, plain. Two capped multiplicative lifts, estimated expanding:
damping state (Undertow pctl ≥ 67) and announced coupon settlements ≥ $90B
(estimated within mid-month/plain buckets only, so the calendar isn't
double-counted). Compounds to P(event by horizon); walk-forward validated
(expanding tables only, truncation-equality unit test) vs climatology with
reliability table; the verdict self-demotes to "trust the dates, not the
levels" when levels stop earning it. A forecast, not evidence: never
weighted into the composite.

**3. Fleet of Forecasts — disagreement as a signal.** *(Superseded at the
branch merge by The Stack — same target, richer walk-forward calibration; the
disagreement meter lives on as the Stack's dispersion output and the Book's
conviction gate. The rule-view label embargo described below was carried into
the Stack's shared event labels.)* Four views now target
the same P(event within 5bd): rule index (mapped through expanding
percentile-bucket event rates, computed with the same predict-then-update
discipline), ML Lab, Tide Tables analogs, Swell 5bd integral. Blend weights
∝ max(1 − Brier/Brier_climatology, 0) from each view's OWN published
walk-forward record — a view that never beat its base rate gets zero weight
(it already self-demoted; averaging it back in would smuggle it past its own
verdict). All-zero skills → the blend IS climatology and says so. The
disagreement meter (max−min) is published as a first-class signal. Every
view's daily forecast now lands in the PIT record — the fleet accrues an
as-published track record from today.

**Surfaces:** new FORECAST tab (Swell curve + Fleet + Tide Tables, moved from
MARKET so the prediction layer has one home), Undertow on RESONANCE (forced
response and free decay side by side), `seiche swell` / `seiche fleet` CLI,
`swell_event_prob` + `fleet_disagree` alert rules, desk-assistant context
pack entries for all three.

**Validation honesty (build-time, this container):** the CI environment's
network policy blocks the upstream data APIs, so v2.3 shipped with the
synthetic-data test suite only (15 new tests, 40 total — including
no-look-ahead truncation-equality for Undertow and Swell, calendar-detection,
lift caps, zero-weight-for-skill-less-views, and disagreement flagging). The
live walk-forward numbers (AUROC/Brier/reliability vs climatology on real
2018–2026 history) compute on first live run and publish themselves on the
FORECAST tab; no live claim is made here that the page won't verify itself.

## Also considered and rejected (v2.3)

- Hawkes self-excitation / branching ratio on funding events — ~20 declustered
  events since 2018 cannot identify a Hawkes MLE; the aftershock intuition is
  partially captured by Undertow's recovery stretch instead. Revisit if a
  micro-event definition (2bp pops, hundreds of events) proves stable.
- Kendall-tau trend tests on EWS indicators — anti-conservative on serially
  correlated series (verified on the synthetic testbed: a stationary control
  produced |tau| = 0.42 with p ≈ 1e-22); percentiles-vs-own-history instead.
- Weighting any forecast (Swell/Fleet/Tide/ML) into the composite — forecasts
  are not evidence of stress; the composite stays a nowcast.

---

# v2.4 addendum — the Navigator, the Communiqué, the TED bridge (2026-07-07)

Driven by one question: how does a tool predict from the whole macro picture
when the target has ~20 events of history? Three answers, each honest about
what it is:

**1. The Navigator (`engines/navigator.py`).** An LLM forecaster made
accountable. The model's whole world is the deterministic context pack; it
must COMMIT one P(funding event, 5bd) per data-day (blob-cached — a re-run
can never revise the morning's number), and the commitment lands in the
hash-chained PIT record. The unique honesty problem is stated in the module
docstring: an LLM member CANNOT be backtested (it has read the history), so
it gets no backtest, no stack membership and no weight anywhere until its
FORWARD record earns a hearing (NAVIGATOR_MIN_RESOLVED). Fails loud without
an endpoint. Surfaces: HELM card, `seiche navigator`, PIT forecasts.views.

**2. The Communiqué (`engines/communique.py` + `sources/fedtext.py`).** FOMC
statements are free, keyless, archived for decades, and stamped with exact
release times — text signals with true vintage discipline. Scoring is a
FROZEN deterministic lexicon (hawk−dove, balance-sheet bias, funding-stress
vocabulary, per 1,000 words) because a scorer that drifts cannot sit under
a vintage-stamped record; the change vs the previous statement is the
signal. Time Machine truncates statements by release date. Degenerate-quiet
history handled: a first break from flat flags even though MAD=0. Statement
URL pattern unverified-live from the build container — collector fails loud
per date, coverage prints.

**3. The TED bridge (`mlpred.build_pretrain_rows`).** Funding stress predates
SOFR: TED (1990–2018 slice) carries 2008/2011/2016 in the same
funding-spread abstraction. TED-era rows join every expanding train slice at
weight 0.30 in the SAME feature slots (spread level/chg/z + the era-invariant
calendar distances); labels are TED pops ≥ 15bp. The fold-skip guard depends
on SOFR-era events ONLY so pooled and solo score identical OOS days —
comparability beats an earlier start — and the published `transfer` block
says whether pretraining helped, either way. TEDRATE fetch unverified-live
from the build container.

Also considered and rejected (v2.4): letting the Navigator into the Stack
(no honest calibration window can exist for a member whose hindcasts are
open-book); LLM-scored statements as the primary text signal (model drift
under a vintage record); scraping FOMC minutes (3-week lag, HTML shape
unverified — statements carry the signal).


---

# v2.5 addendum — Riptide, the Breakwater, Venn–Abers (2026-07-07)

**Riptide (`engines/riptide.py`)** — the design panel's top pick, shipped:
the pop as the unit of analysis. Declustered pops ≥ 4bp (shared PROOF
statistic) become ~independent trials; targets STICKY (half-give-back time
≥ 3bd, 15bd window, unresolved windows carry NO verdict — but early
give-back IS a verdict and is truncation-stable) and ESCALATES (full ≥10bp
event within 10bd). Discriminators as-of the pop-day close: RRP co-sign
(expanding robust z of the ON-RRP daily change — choreography vs scarcity),
calendar bucket, Undertow damping percentile. Tiny expanding logistic
across pops; features with no history yet drop per-fit (the ML Lab rule).
Speaks only on a live pop; flat water is a reading, not an absence.

**The Breakwater (`engines/breakwater.py`)** — the genuinely unshipped idea:
the rescuer as an endogenous player. Every public forecaster treats the Fed
as weather; the Breakwater treats each dated intervention in the public
record (config catalog, editorial dating flagged, append-only) as a revealed
preference: replay the board as of the day BEFORE each announcement
(expanding percentile only) and the distribution of pre-intervention states
IS the reaction function. Outputs the revealed threshold (median/range/n),
live rescue proximity (0–100), and the posture note (a standing facility is
a goalie who never leaves the net). Zero fitted parameters; n≈7 and says so;
context-only — and the caveat that matters: a forecast miss after an
intervention is a SAVE, and an honest scoreboard must say which misses were
saves. Light layer: Time Machine replays it.

**Venn–Abers band (stacker)** — today's ensemble probability now ships with
[p0, p1]: isotonic calibration fitted twice with the current point forced to
each label; the bracket carries finite-sample validity guarantees (Vovk), no
distributional assumptions. Wide band = the OOS record is silent about this
region — uncertainty about the uncertainty, quantified.

Wiring: FORECAST tab (Riptide atop, Breakwater below), HELM (band in the
Stack card), alerts `riptide_sticky` (a live pop classified as a current)
and `breakwater_proximity`, desk-assistant context entries. Tests 58 → 64,
including: the co-sign grammar is learnable on synthetic worlds (AUROC
gate), open windows carry no verdict while early resolutions do, rescues at
stress peaks reveal high thresholds, and Venn–Abers overrides a
miscalibrated point forecast.


---

# v2.6 addendum — "Bathysphere": the physics layer (2026-07-07)

Built in tandem across two sessions, the v2.3 pattern repeated: session A
(PR #6) shipped **Bathymetry** — physics as ESTIMATOR, not metaphor:
reconstruct the equation of motion the pop statistic obeys and derive the
forecast from it. Session B shipped the other three physics engines
(**Merian Modes**, **the Gyre**, **Rogue Wave**), the PHYSICS tab and the
`seiche physics` board, adopted session A's Bathymetry over its own draft,
and merged the two. Doctrine resolution, recorded: session B had drafted a
composite-weighted landscape engine on a 60bd-detrended residual; session
A's design wins because its state variable IS the PROOF pop statistic — a
forecast-layer citizen whose escape probability joins the Stack, never the
composite. The composite weights stay at their v2.3 values; NONE of the
physics engines are composite evidence.

The shared bar, held by all four: mathematics with a pedigree
(Fokker–Planck/Kramers, Koopman operator theory, stochastic thermodynamics,
Takens embedding, extreme value theory), expanding statistics only
(truncation-equality unit tests), walk-forward validation vs climatology
wherever a forecast is made, self-demoting verdicts, small-n CIs. The
quantum-mechanical formalism appears exactly where it is honest
(Hilbert-space operator spectra, the FP↔Schrödinger duality) and nowhere
else. No quantum woo.

## Bathymetry — one engine, four blocks, one estimated object

**1. The Floor (empirical Langevin / Kramers–Moyal).** The daily pop
statistic x (SOFR−IORB minus trailing 5bd median — THE shared PROOF event
statistic, so every layer keeps speaking the same variable) is modeled as a
diffusion dx = D1(x)dt + √(2·D2(x))·dW, with drift and diffusion estimated
from binned conditional moments of observed daily increments (Friedrich &
Peinke's method, standard in turbulence and climate). The effective
potential V(x) = −∫D1 dx is the basin floor made literal: the well is where
the spread rests, V″ at the well is the restoring stiffness, and the barrier
between the well and the event region prints in units of the well's own
diffusion — "the wall is N k_BT high." A flattening well is damping loss
expressed by the dynamics themselves, not by a proxy statistic.

**2. The Spectrum (the quantum block).** The same transitions, on fixed
editorial bins, give an expanding-count Markov transition operator — the
discretized Fokker–Planck propagator. Under detailed balance that operator
maps EXACTLY to a Schrödinger Hamiltonian (the textbook FP↔QM duality):
stationary density = |ground state|², eigenvalue moduli = energy levels
E_k = −ln|λ_k| per business day, and the gap between ground and first
excited state is the inverse of the slowest relaxation time. A closing gap
is critical slowing down measured operator-theoretically — Undertow's
thesis confirmed by an independent estimator on an independent
decomposition. Honesty: markets are not perfectly reversible, so the
mapping is stated as approximate, the spectrum is read on moduli, and the
spectrum is computed on the VISITED sub-chain only — in unvisited corners
of state space the smoothed operator is pure prior, and a prior's slow
random walk across empty bins would masquerade as a slow physical mode
(caught on the synthetic testbed; no evidence, no eigenvalue).

**3. The Arrow (stochastic thermodynamics).** A system in equilibrium
produces no entropy; a driven system does. Schnakenberg entropy production
σ = ½ Σ (J_ij − J_ji) ln(J_ij/J_ji) over stationary probability currents
measures how hard the basin is being forced away from detailed balance, in
nats/day — provably ≥ 0, zero iff reversible. Calm funding markets relax;
stressed ones are pumped. Published as level + expanding percentile, and it
doubles as the printed qualifier on the spectrum block's QM mapping.

**4. The Escape (the prediction machine).** Make the event bins (pop ≥
10bp) absorbing and the operator answers the desk question exactly, no
simulation: P(event within h bd | today's bin) = 1 − e_x′Q^h·1, and the
mean first-passage time (I−Q)⁻¹·1 is the expected business days to the
next funding event under frozen dynamics — Kramers' escape problem solved
on the measured landscape. Walk-forward validated the house way (expanding
counts only, AUROC/Brier vs climatology, reliability table, self-demoting
verdict), and the daily probability joins the Stack as a sixth member with
its own record. On the regime-switching synthetic testbed the walk-forward
first-passage forecast ranks at AUROC 0.78 vs climatology; the live number
computes on first run and publishes itself.
**Deliberate division of labor:** Bathymetry reads ONLY the autonomous
dynamics — the calendar is not an input, because Swell owns the calendar.
The two forecasts disagree exactly where forcing rather than dynamics
drives the risk, and the Stack's dispersion gauge turns that disagreement
into a published signal.

## The other three instruments

**Merian Modes (`engines/merian.py`)** — the seiche eigenmodes, estimated
instead of assumed. Hankel-DMD over the hydrophone's plumbing panel
(expanding-z standardized, trailing windows) estimates the Koopman
operator's spectrum: each mode carries a period, a growth rate ln|λ| and
its CURRENT excitation (amplitude from the latest snapshot). A
high-amplitude ~21bd mode is the month-end forcing seen a second,
independent way (cross-checks Resonance); a growing mode (|λ|>1) with real
amplitude is instability before levels move — published as an expanding
percentile vs the gauge's own history. The linear mode-propagation forecast
is scored vs persistence and expected to lose; the verdict says so and
reframes (modes are structure, not a crystal ball). Koopman–von Neumann
lineage stated for what it is: classical dynamics in Hilbert-space clothes.

**The Gyre (`engines/gyre.py`)** — is prediction possible at all? Takens
embedding + EDM: simplex-projection skill by horizon (the determinism
fingerprint: chaos decays, noise never had skill), a phase-randomized
surrogate gate (preserves linear autocorrelation exactly — what survives is
NONLINEAR structure), the S-map θ test for state dependence, and the S-map
Jacobian's local expansion rate as a live stability gauge. E chosen once on
the warmup segment and frozen (truncation-stable). Relationship to Tide
Tables stated: analogs ask WHICH history rhymes; the Gyre asks whether the
dynamics are deterministic enough to rhyme at all — if the surrogate gate
fails, analog forecasts inherit only linear skill and the page says so.
Deep-layer citizen (blob-cached); forecast-context, never composite evidence.

**Rogue Wave (`engines/roguewave.py`)** — the tail law. POT/GPD
(probability-weighted moments, no scipy) on the SAME declustered pop
statistic as PROOF (cluster maxima, not first days — EVT wants magnitudes;
the difference from PROOF's lead-time convention is a printed caveat).
Return levels (1/5/10y waves) and P(pop ≥ x within h) for severities beyond
the sample maximum — the number Swell's empirical exceedance tables cannot
produce, with bootstrap CIs, a threshold-sensitivity table, and annual
expanding ξ refits answering "is the tail getting heavier as the buffers
drain?". Context engine: never weighted into the composite.

## Wiring

Bathymetry: deep layer + blob cache (VERSION bumped to 0.4.0 so no stale
pre-physics blob serves), Stack member `bathy`, PIT record via the Stack's
members_now, FORECAST tab card (potential landscape SVG with the ball at
today's state, τ/entropy time series, energy levels), `seiche bathymetry`
CLI, `bathymetry_event_prob` alert rule, desk-assistant context entries.
The layer: new PHYSICS tab (all four engines; custom XY SVG for the
potential landscape and skill-decay curves — uPlot is date-axis only),
`seiche physics` CLI board, `merian_instability` alert (growth pctl ≥ 95
with g > 0), brief watchlist lines, desk-assistant context entries for all
four. Merian and Rogue Wave live in the light layer — Time-Machine
replayable; Bathymetry and the Gyre live in the deep layer.
Bathymetry tests (in test_engines.py) 64 → 70: recovers a known OU well
(drift sign, stiffness, well location), spectral gap closes as φ→1, entropy
production ≥ 0 and ~4× larger for a cyclically driven world than a
reversible one, a flat hot well escapes faster than a deep calm one (with
the walk-forward required to rank), truncation-equality (no look-ahead),
refuses short history. Merian/Gyre/Rogue Wave ship their own test files
under the same invariants.

## Also considered and rejected (v2.6)

- Path-integral Monte Carlo for the forward distribution — the absorbing-
  boundary matrix computation gives the SAME quantity exactly and
  deterministically; simulation would add seed-dependence to a record that
  must replay bit-identically.
- Actual quantum computing / quantum amplitude estimation / anything sold
  as "quantum finance" — buzzword, not estimator: no keyless QPU meets the
  provenance bar, and nothing in a 19-state first-passage problem needs
  one. The quantum content here is the legitimate FP↔Schrödinger duality
  and the Koopman operator formalism, stated with their caveats printed.
- Data-dependent (quantile) state bins — sharper resolution where the data
  lives, but bin edges that move when data arrives would leak the future
  into the past; fixed editorial edges instead (config).
- Hawkes self-excitation (again): still ~20 declustered events; Rogue
  Wave's cluster handling covers the aftershock intuition at the magnitude
  level. Revisit with a micro-event definition.
- Wavelet ridge tracking for time-varying modes: Hankel-DMD's trailing
  window already yields a mode history at 5bd cadence; a second
  time-frequency view would be decoration.
- Random-matrix (Marchenko–Pastur) cleaning of the Hydrophone correlation
  panel — real candidate, but it upgrades an existing diagnostic rather
  than adding a prediction; deferred to the ideas ledger.
