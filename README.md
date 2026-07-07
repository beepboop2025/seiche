# SEICHE

> A **seiche** is a standing wave in an enclosed body of water — invisible from the
> shore, until it sloshes over the edge. Funding stress behaves the same way.

**Seiche is a funding-stress, positioning and divergence terminal** for the dollar
funding system — US money markets, the Treasury capital-market complex, the global
basins connected to them through the swap lines, and the offshore-dollar crypto
basin moored to the T-bill market through stablecoins. Zero data cost: built
entirely on free, keyless public APIs (FRED, NY Fed Markets, OFR STFM, Treasury
FiscalData, CFTC, ECB Data Portal, DeFiLlama, Coinbase Exchange).

Every 2025–26 stress event (Sep 15 2025 tax-date squeeze, Oct/Dec 2025 record SRF
draws, Apr 2025 basis unwind) was front-run by *plumbing* signals while price screens
looked calm. Incumbent tools either have the data with no opinion (Bloomberg, $32k/yr)
or authority with no synthesis (OFR/NY Fed dashboards). Seiche is the opinionated
fusion layer: forward-looking, alerting-ready, provenance-honest — and v2 adds the
layer none of them have: **honest evidence about itself**.

## v2 "Deep Water" — engines, analytics layers, and the Book

> **v2.3 "Letters of Marque"** adds the layer that makes every other layer
> accountable: **The Stack** (walk-forward ensemble of all three event
> forecasters + The Tell, with a disagreement gauge), **The Book** (HELM tab —
> explicit daily positions on 2y/10y duration proxies, S&P and BTC over a
> T-bill base, walk-forward P&L with costs, block-bootstrap Sharpe CIs and
> mandatory benchmarks, verdict printed even when it loses), a **hash-chained
> as-published track record** shipped inside the static publish (nobody,
> including the operator, can quietly rewrite a bad month), and the **Far
> Basin** — Palimpsest's censorship-fear channel (palimpsest.info), a policy
> confession signal no market data vendor carries, honestly quarantined until
> it accrues testable history. Strategy doc: [docs/STRATEGY.md](docs/STRATEGY.md).

| Engine | Question it answers |
|---|---|
| **Kink Engine** | Where does reserve scarcity start, and how many days away is it at the current drain rate? (live hockey-stick fit of SOFR−IORB vs reserves/GDP) |
| **Liquidity Weather** | What does the reserve path look like 6 weeks out — and which auction-settlement days land on thin ice? (TGA seasonal model + Fed drift + settlement calendar + backtested error bands) |
| **Tail Seismograph** | Are the P99 tails of SOFR/TGCR/BGCR detaching from the median — the first tell of every squeeze? |
| **Echo Engine** | Does today's 30-day trajectory rhyme with the run-up to any historical stress episode? |
| **Tide Tables** ★ | What happened next, every time the water looked like this? Markets rhyme, so forecast like a tide table: the k nearest analogs of today's trailing state trajectory over ALL history (labeled or not, expanding-z — no look-ahead) publish their actual forward spread paths as a fan, the share followed by a funding event within 5bd (Wilson CI vs climatology), a NOVELTY gauge ("the board has never looked like this" is its own signal, and flags the fan as extrapolation), and a walk-forward hindcast that says honestly whether analogs beat the base rate. |
| **RV X-Ray** | How big is the leveraged Treasury RV complex, and what does a 5/15/30bp shock do to it? |
| **Crowding** | Where are leveraged funds most crowded relative to their own history (UST curve, SOFR/FF futures, S&P)? |
| **Auction Digestion** | Is the market choking on Treasury supply? |
| **Warehouse** | How full is the primary-dealer balance sheet — the shock absorber of last resort? (NY Fed PD stats by maturity bucket) |
| **Resonance Engine** ★ | *The seiche made literal:* does the same calendar forcing (month-end, quarter-end, year-end, tax dates) produce a bigger slosh than it used to? Amplification = damping loss = fragility rising while levels look calm. |
| **Hydrophone Array** ★ | How connected is the plumbing right now? (absorption ratio over 11 funding series + a live lead-lag map of which pipe is upstream) |
| **Global Basin Coupling** ★ | Are the US, euro-area, UK, India (FX channel) and crypto basins moving as one tide? Plus the global confession channel: USD swap-line draws (test operations excluded). |
| **Stablecoin Moorings** ★ | The offshore-dollar basin's tie lines: peg deviations (USDT history + live board), total-circulation flows ($200B+ of T-bills behind them), and the 24/7 BTC canary — crypto trades when funding markets sleep. |
| **ML Lab** | Learned P(funding event within 5bd): walk-forward with a 5bd boundary embargo, benchmarked against climatology AND the rule-based index, reliability table + decision-utility scoring published. Verdict at build: ranks better than the rule (OOS AUROC 0.826 vs 0.806; 0.812 on the orthogonal feature set) but probability levels don't beat climatology — use for ranking/alerting, not literal odds. The verdict self-updates. |
| **Station-Keeping** ★ | Orbit-determination transfer: propagate the reserve system's expected state (fiscal seasonal, calendar buckets, trailing drift), CUSUM the innovation residuals, flag unmodeled "burns" — debt-ceiling cash games, RMP pace changes — often before they're narrated. Doubles as the Weather model's health monitor. |
| **The Stack** ★ | One P(funding event, 5bd) from the whole fleet: rule index, ML Lab and Tide Tables calibrated per-member and blended walk-forward (with regime dummies, ~8 params on purpose). Publishes the equal-weight mean instead whenever the fitted stack fails to beat it OOS, and publishes member DISPERSION — when the fleet disagrees, conviction drops. |
| **The Book** ★ | The signal made accountable (HELM tab): a FROZEN rulebook maps the ensemble to explicit daily long/short/flat weights (2y/10y UST duration proxies, S&P 500, BTC over T-bill cash; hysteresis bands, a disagreement gate, vol targeting, per-sleeve cost haircuts), then walk-forward P&L — signal t earns returns t+1, enforced in one place and unit-tested — with stationary-block-bootstrap Sharpe CIs, Newey–West t-stats, per-episode attribution, doubled-cost rerun, and benchmarks through the identical pipeline. If it doesn't beat the static mix after costs, the page says so in bold. Every day's positions land in a **hash-chained as-published ledger** carried by the published site — tamper-evident by construction. Paper proxy; not advice. |
| **Far Basin** ★ | The policy-fear channel: Palimpsest (palimpsest.info) measures what the Chinese state rushes to delete — the DDTI deletion-threat index, newly-targeted terms, the Generative Firewall Index — CI-published, keyless, mirrored on GitHub raw. A confession channel one basin further out, carried by no market data vendor. Honest scope: days old as a public series, so it accrues locally and stays QUARANTINED (context only, never in the composite, never a model feature) until it clears 250 daily observations. |
| **Seiche Index** | One 0–100 number with full decomposition and a regime call: CALM / EROSION / STRAIN / STRESS. |

★ = methods invented for this tool.

**The desk assistant**: `seiche ask "why is the index elevated?"` (or the Ask box on
BOARD) answers strictly from a deterministic context pack of the live board — every
number cited to its engine and as-of date, "not on the board" instead of improvisation.
Routed through free-llm-router's free tiers, or any OpenAI-compatible endpoint via
`SEICHE_LLM_BASE_URL`; with neither configured it returns the context pack itself.

**The analytics layers on top:**

- **The Tell** — plumbing percentile minus market-priced-stress percentile (VIX, HY/IG
  OAS, rates vol). Positive = the basin is sloshing and the screens haven't noticed.
  The whole thesis in one tradeable number.
- **Turn Barometer** — forecasts the *next* month/quarter-end turn's severity with
  leave-one-out cross-validation, always benchmarked against a naive forecast. When
  the model can't beat naive, it says so and publishes naive instead.
- **Playbook** — what S&P/VIX/OAS/yields did the last N times the board looked like
  this, in native units, with n printed. Decision support, not advice.
- **PROOF** — the backtest lab: the index rebuilt with expanding-window statistics
  only (no look-ahead — enforced by a unit test), recall/precision with Wilson 95%
  intervals, run-level precision (alert days are serially correlated; runs are the
  honest trials), episode-by-episode lead times *including the ones it missed* —
  and the **orthogonal signal test**: the same event-capture with the target's own
  variable family (spread/tails) removed from the signal. At build: orthogonal
  recall 69% [CI 42–87] vs 62% full, with the structural components alone at the
  98th–100th percentile 42 days before the Sep/Dec-2025 squeezes. The claim is
  causal structure, not autocorrelation.
- **Time Machine** — replay the whole board as of any date since ~2018. Replayed to
  **Sep 12 2019**, the board reads EROSION with reserves $576B below the kink and
  flags **Sep 16 2019** — the exact day the repo market broke — as a crunch window.

Principles: **no naked numbers** (every value carries source + as-of + staleness),
**fail-loud** (a dead feed shows as DEAD and reduces published coverage — it never
silently vanishes), **honest lags** (COT is T+3 by construction; shown, not hidden),
**honest scope** (markets without a qualifying free feed — Japan, China, Russia,
Africa — are stated as out of scope, not faked in).

## Run it

```bash
# backend (Python 3.11+)
cd backend
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/uvicorn seiche.api:app --port 8787

# frontend (dev)
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api to :8787)

# or production single-process: npm run build, then uvicorn serves frontend/dist at /
```

First load is slow (cold fetch of several years of history); everything after is
served from the SQLite cache with cadence-aware TTLs.

## The operator CLI

```bash
seiche pull               # force-refresh, print the index line
seiche brief --save       # this morning's desk note (markdown, archived to data/briefs/)
seiche alert              # evaluate alert rules once (cron/launchd-friendly; exit 2 = fired)
seiche watch -i 1800      # pull + alert on a loop
seiche replay 2019-09-12  # Time Machine in the terminal
seiche backtest           # PROOF summary
seiche ml                 # ML Lab: event probability + honest validation
seiche analogs            # Tide Tables: nearest historical analogs + forward fan
seiche book               # the Book: today's positions + walk-forward P&L verdict
seiche ask "…"            # desk assistant, grounded in the live board
seiche serve              # API + UI
```

Alerts dedupe per state in SQLite, notify via macOS notification and optional
webhook (`SEICHE_WEBHOOK_URL` — Slack/Telegram/ntfy style `{"text": ...}`).
A launchd template lives in `ops/com.seiche.watch.plist`.

## Tuning the editorial voice

`backend/seiche/config.py` quarantines every judgment call: composite weights, regime
thresholds, resonance/turn/tell parameters, alert rules, the episode library, contract
DV01s. The math never hides an opinion.

## Non-goals

No paid data, no auth, no intraday ticks. Daily cadence + operation results is the
honest granularity of the free stack. Backtests use final-vintage data (weekly H.4.1
aggregates are lightly revised; the caveat is printed on the PROOF page). From v2
onward Seiche also accrues a true as-published point-in-time record (`/api/pit`)
that no reconstruction can be accused of polishing. Not investment advice.
