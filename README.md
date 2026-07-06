# SEICHE

> A **seiche** is a standing wave in an enclosed body of water — invisible from the
> shore, until it sloshes over the edge. Funding stress behaves the same way.

**Seiche is a funding-stress, positioning and divergence terminal** for the dollar
funding system — US money markets, the Treasury capital-market complex, and the
global basins connected to them through the swap lines. Zero data cost: built
entirely on free, keyless public APIs (FRED, NY Fed Markets, OFR STFM, Treasury
FiscalData, CFTC, ECB Data Portal).

Every 2025–26 stress event (Sep 15 2025 tax-date squeeze, Oct/Dec 2025 record SRF
draws, Apr 2025 basis unwind) was front-run by *plumbing* signals while price screens
looked calm. Incumbent tools either have the data with no opinion (Bloomberg, $32k/yr)
or authority with no synthesis (OFR/NY Fed dashboards). Seiche is the opinionated
fusion layer: forward-looking, alerting-ready, provenance-honest — and v2 adds the
layer none of them have: **honest evidence about itself**.

## v2 "Deep Water" — twelve engines, five analytics layers, nine tabs

| Engine | Question it answers |
|---|---|
| **Kink Engine** | Where does reserve scarcity start, and how many days away is it at the current drain rate? (live hockey-stick fit of SOFR−IORB vs reserves/GDP) |
| **Liquidity Weather** | What does the reserve path look like 6 weeks out — and which auction-settlement days land on thin ice? (TGA seasonal model + Fed drift + settlement calendar + backtested error bands) |
| **Tail Seismograph** | Are the P99 tails of SOFR/TGCR/BGCR detaching from the median — the first tell of every squeeze? |
| **Echo Engine** | Does today's 30-day trajectory rhyme with the run-up to any historical stress episode? |
| **RV X-Ray** | How big is the leveraged Treasury RV complex, and what does a 5/15/30bp shock do to it? |
| **Crowding** | Where are leveraged funds most crowded relative to their own history (UST curve, SOFR/FF futures, S&P)? |
| **Auction Digestion** | Is the market choking on Treasury supply? |
| **Warehouse** | How full is the primary-dealer balance sheet — the shock absorber of last resort? (NY Fed PD stats by maturity bucket) |
| **Resonance Engine** ★ | *The seiche made literal:* does the same calendar forcing (month-end, quarter-end, year-end, tax dates) produce a bigger slosh than it used to? Amplification = damping loss = fragility rising while levels look calm. |
| **Hydrophone Array** ★ | How connected is the plumbing right now? (absorption ratio over 11 funding series + a live lead-lag map of which pipe is upstream) |
| **Global Basin Coupling** ★ | Are the US, euro-area and UK basins moving as one tide? Plus the global confession channel: USD swap-line draws (test operations excluded). |
| **Seiche Index** | One 0–100 number with full decomposition and a regime call: CALM / EROSION / STRAIN / STRESS. |

★ = methods invented for this tool.

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
  only (no look-ahead — enforced by a unit test), recall/precision vs base rate,
  episode-by-episode lead times *including the ones it missed*.
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
