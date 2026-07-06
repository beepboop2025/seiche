# SEICHE

> A **seiche** is a standing wave in an enclosed body of water — invisible from the
> shore, until it sloshes over the edge. Funding stress behaves the same way.

**Seiche is a funding-stress and leveraged-positioning early-warning terminal** for US
money markets and the Treasury capital-market complex. One pane, seven engines, zero
data cost — built entirely on free, keyless public APIs (FRED, NY Fed Markets, OFR
STFM, Treasury FiscalData, CFTC).

Every 2025–26 stress event (Sep 15 2025 tax-date squeeze, Oct/Dec 2025 record SRF
draws, Apr 2025 basis unwind) was front-run by *plumbing* signals while price screens
looked calm. Incumbent tools either have the data with no opinion (Bloomberg, $32k/yr)
or authority with no synthesis (OFR/NY Fed dashboards). Seiche is the opinionated
fusion layer: forward-looking, alerting-ready, provenance-honest.

## The seven engines

| Engine | Question it answers |
|---|---|
| **Kink Engine** | Where does reserve scarcity start, and how many days away is it at the current drain rate? (live hockey-stick fit of SOFR−IORB vs reserves/GDP) |
| **Liquidity Weather** | What does the reserve path look like 6 weeks out, and which dates are crunch windows? (TGA seasonal model + Fed balance-sheet drift + backtested error bands) |
| **Tail Seismograph** | Are the P99 tails of SOFR/TGCR/BGCR detaching from the median — the first tell of every squeeze? |
| **Echo Engine** | Does today's 30-day trajectory rhyme with the run-up to any historical stress episode? (fingerprint matching vs Sep-2019, Mar-2020, SVB, Apr/Sep/Dec-2025) |
| **RV X-Ray** | How big is the leveraged Treasury RV complex right now, and what does a 5/15/30bp shock do to it? (CFTC TFF × repo volumes, transparent method) |
| **Auction Digestion** | Is the market choking on Treasury supply? (per-tenor z-scores of bid-to-cover / dealer takedown / indirects) |
| **Seiche Index** | One 0–100 number with full decomposition and a regime call: CALM / EROSION / STRAIN / STRESS. |

Principles: **no naked numbers** (every value carries source + as-of + staleness),
**fail-loud** (a dead feed shows as DEAD and reduces published coverage — it never
silently vanishes), **honest lags** (COT is T+3 by construction; shown, not hidden).

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

## Tuning the editorial voice

`backend/seiche/config.py` quarantines every judgment call: composite weights, regime
thresholds, crunch cushions, the episode library, contract DV01s. The math never
hides an opinion.

## Non-goals (v1)

No paid data, no auth, no intraday ticks. Daily cadence + operation results is the
honest granularity of the free stack. Not investment advice.
