# SEICHE

[![sealed record](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.seiche.info%2Fapi%2Fbadge%2Frecord)](https://api.seiche.info/api/notary)

> A **seiche** is a standing wave in an enclosed body of water — invisible from the
> shore, until it sloshes over the edge. Funding stress behaves the same way.

**Seiche is a free, open source (AGPL-3.0-or-later) money-, foreign-exchange and
capital-market evidence terminal** organized around the dollar funding system —
US money markets, 22 public H.10 currency reference series, the Treasury
capital-market complex, the global basins connected through swap lines, and the
offshore-dollar crypto basin moored to the T-bill market through stablecoins. The core US board and
USD desk use free official/public APIs (FRED, NY Fed Markets, OFR STFM, Treasury
FiscalData, CFTC, ECB Data Portal, DeFiLlama, Coinbase Exchange). The global
market-pack catalog can also declare licensed or tenant sources, but public
outputs obey each source's redistribution policy and never expose restricted raw
values.

Seiche publishes a construction-PIT historical diagnostic—with misses and
eligibility limits kept visible—and is now accruing a separate as-published
forward record that can be verified instead of reconstructed. Licensed terminals
provide much broader real-time data and execution workflows, while OFR and NY Fed
dashboards provide authoritative source views. Seiche is a narrower, opinionated
research layer: forward-looking, alerting-ready, and provenance-honest. It is not
a Bloomberg/Reuters replacement, a real-time quote service, or an execution venue.

## The lab

Seiche is one of three altitudes in a single liquidity lab that fills that gap:
**Seiche** reads the plumbing, **[LiquiLens](https://liquilens.in)** ranks the
institutions standing on it, and **Undertow** prices market liquidity itself
(who provides depth in each segment, what an exit at position size costs
today). The wiring between the three is real, not a brochure: the desk
assistant pulls the LiquiLens failure board over MCP, the MARKET tab renders
Undertow's published pack live, and the Windfetch engine reads the FETCH pack
built in the Undertow repo back into this board. On names: the internal
composite engine called "undertow" (critical slowing down,
`engines/undertow.py`) is unrelated to the Undertow sister product.

## Use Seiche everywhere

Seiche is distributed as software, an MCP service, a research dataset, and
machine-readable catalog metadata. Status is receipt-based: **listed** means a
durable public record and receipt exist, **prepared** means an owner-controlled
submission or publication remains, and **blocked** means a named prerequisite
must clear first. “Usable in repo” describes local artifact readiness, not a
ledger status. The auditable source of truth is
[`distribution/submissions.csv`](distribution/submissions.csv).

| Surface | What is available | Status |
|---|---|---|
| **OpenBB** | Typed provider and `obb.seiche` router for funding stress, world markets, and data health ([extension](integrations/openbb/)) | Usable/tested in repo; signed 0.1.0 PyPI publication and current ecosystem-list submission packet prepared |
| **Zenodo** | Release deposition metadata, citation identity, and related-source records ([metadata](.zenodo.json)) | Prepared; no deposit or DOI claimed |
| **Hugging Face** | Rights-reviewed direct-OFR dataset card and staging layout ([dataset card](distribution/datasets/huggingface/README.md)) | Validated draft; upload prepared |
| **Kaggle** | Dataset metadata and reference-only staging layout ([metadata](distribution/datasets/kaggle/dataset-metadata.json)) | Validated draft; upload prepared |
| **Smithery** | Owner-published hosted endpoint entry ([live record](https://smithery.ai/servers/mrinallovesbhature/seiche)) | Listed but stale; authenticated rescan pending |
| **MCP directories** | Official Registry, Glama, Smithery, and eight additional live indexes ([dated inventory](distribution/MCP_DIRECTORIES.md)) | 11 live records; claim/freshness gaps tracked |
| **Research notebooks** | Commit-pinned, hash-checking direct-OFR workflow ([notebook](notebooks/seiche_direct_ofr_research.ipynb)) | Usable in repo |
| **Python / R / JavaScript** | Zero-secret world-markets API clients with evidence-contract checks ([clients](clients/)) | Usable in repo |
| **Market Atlas + structured corpus** | Interactive canonical market observations, rights-aware dataset receipts, full normalized BIS records, Seiche partitions, public API and MCP ([contract](docs/MARKET_CORPUS.md)) | Live gateway; protected exports remain restricted and bulk records use bounded snapshot cursors |
| **Docker** | Distroless, non-root, read-only Compose image and signed GHCR publication workflow ([guide](docs/DISTRIBUTION.md)) | Built and tested locally; GHCR prepared |
| **Academic dataset** | 10 direct-OFR series and 11,163 audited observations, excluding restricted and derived rows ([research kit](distribution/datasets/README.md)) | Validated draft; not submitted |
| **Data catalogs** | Native-validated Croissant/Frictionless, graph-parsed DCAT 3/RO-Crate 1.3, and a DOI-free DataCite planning draft ([metadata kit](distribution/datasets/)) | Validated as labeled; publication prepared |
| **AI integrations** | Hosted MCP configs for Claude Code, Cursor, VS Code, Gemini CLI, and Codex; separate OpenAI workspace/submission guidance ([configs](integrations/mcp-clients/)) | Configs usable; OpenAI listing prepared |
| **PyPI** | Python package and stdio MCP server (`pip install seiche`) | Repository identity 0.12.3; immutable availability is authoritative only on the linked PyPI project |

Twelve evidence tools remain anonymous and free. Five compute-heavy tools are
account-gated; client and catalog copy must preserve that boundary. See the
[distribution and container trust guide](docs/DISTRIBUTION.md) for verification
and release invariants.

## v2 "Deep Water" — an accountable engine fleet, layered analytics, one terminal

> **v2.3 "Letters of Marque"** (built in tandem across two sessions) adds the
> forecast layer and the layer that makes every other layer accountable:
> **Undertow** (critical slowing down — the basin's damping, measured on
> ordinary days), the **Swell Forecast** (the funding-stress forward curve —
> P(pop ≥ x bp) by date, six weeks out, from the public forcing calendar),
> **The Stack** (walk-forward ensemble of every event forecaster — rule, ML,
> analogs, Swell — plus The Tell, with a disagreement gauge), **The Book**
> (HELM tab — explicit daily positions on 2y/10y duration proxies, S&P and
> BTC over a T-bill base, walk-forward P&L with costs, block-bootstrap Sharpe
> CIs and mandatory benchmarks, verdict printed even when it loses), a
> **hash-chained as-published track record** shipped inside the static
> publish (nobody, including the operator, can quietly rewrite a bad month),
> and the **Far Basin** — Palimpsest's censorship-fear channel
> (palimpsest.info), a policy confession signal no market data vendor
> carries, honestly quarantined until it accrues testable history.

| Engine | Question it answers |
|---|---|
| **Kink Engine** | Where does reserve scarcity start, and how many days away is it at the current drain rate? (live hockey-stick fit of SOFR−IORB vs reserves/GDP) |
| **Liquidity Weather** | What does the reserve path look like 6 weeks out — and which auction-settlement days land on thin ice? (TGA seasonal model + Fed drift + settlement calendar + backtested error bands) |
| **Tail Seismograph** | Are the P99 tails of SOFR/TGCR/BGCR detaching from the median — the first tell of every squeeze? |
| **USD Money Market Desk** ★ | Seven institutional-depth sections over the US cash system: policy corridor and overnight spreads; SOFR/TGCR/BGCR distributions and tails; repo-segment rates and volumes; CP−Treasury spreads; bills; liquidity buffers and Fed facilities; and MMF repo plumbing. Cross-source arithmetic uses exact common dates, repo aggregates require a fixed component set, and the descriptive NORMAL/WATCH/STRAIN/STRESS label uses a dependence-robust family-wise adjustment rather than an uncalibrated maximum. |
| **Global Money Market Atlas** ★ | A licence-aware, native-frequency catalog and available-evidence comparison across 11 registered monetary-area packs, plus a dated source-audited discovery ledger of 52 additional monetary areas across nine regions. Registration and discovery are not live coverage. Each available market is compared only with its own history and can be `LIVE_REFERENCE`/`STALE_REFERENCE`, `DERIVED_CONTEXT`, `POLICY_ONLY`, or `DECLARED_UNAVAILABLE`; no global stress score is manufactured from unlike rate levels. |
| **Echo Engine** | Does today's 30-day trajectory rhyme with the run-up to any historical stress episode? |
| **Tide Tables** ★ | What happened next, every time the water looked like this? Markets rhyme, so forecast like a tide table: the k nearest analogs of today's trailing state trajectory over ALL history (labeled or not, expanding-z — no look-ahead) publish their actual forward spread paths as a fan, the share followed by a funding event within 5bd (Wilson CI vs climatology), a NOVELTY gauge ("the board has never looked like this" is its own signal, and flags the fan as extrapolation), and a walk-forward hindcast that says honestly whether analogs beat the base rate. |
| **RV X-Ray** | How big is the leveraged Treasury RV complex, and what does a 5/15/30bp shock do to it? |
| **Crowding** | Where are leveraged funds most crowded relative to their own history (UST curve, SOFR/FF futures, S&P)? |
| **Auction Digestion** | Is the market choking on Treasury supply? |
| **Warehouse** | How full is the primary-dealer balance sheet — the shock absorber of last resort? (NY Fed PD stats by maturity bucket) |
| **Resonance Engine** ★ | *The seiche made literal:* does the same calendar forcing (month-end, quarter-end, year-end, tax dates) produce a bigger slosh than it used to? Amplification = damping loss = fragility rising while levels look calm. |
| **Undertow** ★ | The free-decay half of the resonance physics: critical slowing down (Scheffer et al.), measured continuously. Rising lag-1 autocorrelation + variance of the detrended spread/tail and a stretching recovery half-life after everyday pops = the basin losing damping on days when NOTHING is happening. Expanding percentiles only; weighted into the composite as structural evidence. |
| **Swell Forecast** ★ | A 42-business-day funding-stress forward curve: P(SOFR−IORB pop ≥ 2/5/10/20bp) by date, built from the public forcing calendar (turn/tax/settlement days each keep their full expanding distribution of historical pops), lifted by the live damping state and announced coupon settlements. It compounds to P(event by horizon), is walk-forward scored vs climatology on final-vintage history with the reliability table printed, and self-demotes to "trust the dates, not the levels" when the levels stop earning it. |
| **Hydrophone Array** ★ | How connected is the plumbing right now? (absorption ratio over 11 funding series + a live lead-lag map of which pipe is upstream) |
| **Global Basin Coupling** ★ | Are the US, euro-area, UK, India (FX channel) and crypto basins moving as one tide? Plus the global confession channel: USD swap-line draws (test operations excluded). |
| **The Estuary / Passage** ★ | Where FX settlement and physical inventory become money-market cash demand. Twenty-two H.10 currencies, advanced/emerging dollar indices, daily energy and monthly IMF commodity breadth are normalized into an upstream-pressure reading, then compared with SOFR−IORB and commercial-paper spreads. Its differentiator, **The Passage**, chooses each candidate target and lag on the first 60% of aligned history and calls it `earned` only if direction and magnitude survive the untouched final 40%; unstable stories print `not earned`. De-clustered analog outcomes, BIS PvP settlement structure and an editable cash-conversion lab keep statistical evidence, structural benchmarks and scenarios visibly separate. Context only, never a composite input. |
| **Oil × Funding** ★ | The barrel's balance sheet, in both directions: WTI/Brent, CP−bill and SOFR−IORB evidence; live Cushing stocks and Brent−WTI basis kept separate from dated capacity, benchmark and chokepoint references; a rolling change-on-change coupling diagnostic; the observed mechanical carry hurdle; energy/core CPI into IORB; broad foreign-official dollar-parking proxies for the recycling channel; and an editable, explicitly scenario-only lab for cargo credit, margin calls, RBI liquidity absorption and OMC commercial-paper demand. Context only, never a composite input. |
| **[Ballast](docs/BALLAST.md)** ★ | The energy-futures cash ledger inside Oil × Funding. Exact CFTC contract codes for WTI and Henry Hub join weekly open interest, trader classes and paying-side concentration to public benchmark moves and contract multipliers; EIA commercial crude stocks supply the physical-collateral leg; SOFR−IORB and CP−bill show a separate funding-amplifier overlay. The output is a bounded gross mark-displacement proxy—not an observed margin call—and the headline takes the worst own-history commodity/physical percentile without blending or letting funding alone manufacture a commodity alert. Typed handoffs route exit cost to Undertow and named-institution exposure to LiquiLens. Context only, never a composite input. |
| **Stablecoin Moorings** ★ | The offshore-dollar basin's tie lines: peg deviations (USDT history + live board), total-circulation flows ($200B+ of T-bills behind them), and the 24/7 BTC canary — crypto trades when funding markets sleep. |
| **ML Lab** | Learned P(funding event within 5bd): walk-forward with a 5bd boundary embargo, benchmarked against climatology AND the rule-based index, reliability table + decision-utility scoring published. Verdict at build: ranks better than the rule (OOS AUROC 0.826 vs 0.806; 0.812 on the orthogonal feature set) but probability levels don't beat climatology — use for ranking/alerting, not literal odds. The verdict self-updates. |
| **Station-Keeping** ★ | Orbit-determination transfer: propagate the reserve system's expected state (fiscal seasonal, calendar buckets, trailing drift), CUSUM the innovation residuals, flag unmodeled "burns" — debt-ceiling cash games, RMP pace changes — often before they're narrated. Doubles as the Weather model's health monitor. |
| **Riptide** ★ | The pop prognosis — the one morning the whole desk asks the same question, answered: *chop or current?* Every declustered spread pop becomes a trial; the discriminators (RRP co-sign — a pop WITHOUT its mechanical quarter-end co-move is genuine scarcity, the 2025 signature; calendar bucket; damping state) feed a deliberately tiny walk-forward logistic that classifies the live pop as calendar mechanics or the start of a squeeze, with P(sticky) and P(escalates) scored pop-by-pop against the base rate on final-vintage history. Speaks only when there is a live pop; flat water is itself the reading. |
| **The Breakwater** ★ | The rescuer modeled — the feature no forecaster ships: the Fed is not weather, it is a PLAYER, and every intervention in the public record is a confession of where its pain threshold sat that day. A zero-parameter revealed-preference catalog (repo ops '19, QE '20, SRF '21, BTFP '23, QT taper '24, RMPs '25) compared with the final/current-vintage construction-PIT board for the day before each announcement yields a diagnostic threshold and a live **rescue proximity** gauge — which cuts both ways, and the engine says so: a forecast miss after an intervention is a save, not a false alarm. |
| **Bathymetry** ★ | The basin floor mapped from the water's motion — the physics program end to end. The daily pop statistic is treated as a diffusion and its dynamics are RECONSTRUCTED from the data (Kramers–Moyal / empirical Langevin): drift → the **effective potential** (the well the spread rests in, its restoring stiffness, and the escape barrier printed in units of thermal energy k_BT); the binned transition operator → the **quantum-dual energy spectrum** (Fokker–Planck ↔ Schrödinger: stationary density = ground state, eigenvalue moduli = energy levels, spectral gap = inverse of the slowest relaxation time — critical slowing down measured operator-theoretically, corroborating Undertow by an independent estimator); stationary probability currents → **entropy production** (Schnakenberg, nats/day — the arrow of time: a calm basin relaxes, a stressed one is pumped); and absorbing-boundary **first passage** → P(funding event within h bd | today's state) and the expected business days to the next event, Kramers' escape problem solved exactly on the measured landscape, no simulation. Expanding counts only, walk-forward scored vs climatology on final-vintage history, and the daily probability joins the Stack as its own member with its own record. |
| **The Stack** ★ | One P(funding event, 5bd) from the whole fleet: rule index, ML Lab, Tide Tables, Swell and Bathymetry calibrated per-member and blended walk-forward (with regime dummies, ~10 params on purpose). Publishes the equal-weight mean instead whenever the fitted stack fails to beat it OOS, publishes member DISPERSION — when the fleet disagrees, conviction drops — and wraps today's number in a **Venn–Abers calibrated band** [p0, p1] with finite-sample validity guarantees: not "our probability is 7%" but "the calibrated probability is provably between these bounds". |
| **The Book** ★ | The signal made accountable (HELM tab): a FROZEN rulebook maps the ensemble to explicit daily long/short/flat weights (2y/10y UST duration proxies, S&P 500, BTC over T-bill cash; hysteresis bands, a disagreement gate, vol targeting, per-sleeve cost haircuts), then walk-forward P&L — signal t earns returns t+1, enforced in one place and unit-tested — with stationary-block-bootstrap Sharpe CIs, Newey–West t-stats, per-episode attribution, doubled-cost rerun, and benchmarks through the identical pipeline. If it doesn't beat the static mix after costs, the page says so in bold. Every day's positions land in a **hash-chained as-published ledger** carried by the published site — tamper-evident by construction. Paper proxy; not advice. |
| **Merian Modes** ★ | *(v2.6 "Bathysphere")* The seiche eigenmodes, estimated instead of assumed. Merian's formula gives a real basin's standing-wave period from its geometry; we go the other way — Hankel-DMD (a finite-dimensional estimate of the Koopman operator: classical dynamics in the Hilbert-space clothes of Koopman–von Neumann mechanics) reads the funding basin's actual modes out of the plumbing panel: period, growth rate, current excitation. A mode with \|λ\| > 1 is a growing oscillation — instability visible before levels move; the ~21bd mode is the month-end forcing seen a second, independent way. The linear mode-propagation forecast is scored vs persistence and self-demotes (modes are structure, not a crystal ball). |
| **The Gyre** ★ | *(v2.6)* Is prediction possible at all? Takens delay embedding + empirical dynamic modeling (Sugihara): simplex-projection skill by horizon (chaos decays, noise never had skill), a phase-randomized surrogate gate for determinism beyond linear autocorrelation, the S-map θ test for state-dependent (nonlinear) dynamics, and the S-map Jacobian's local expansion rate \|λ\| as a live "the water is locally unstable" gauge. Tide Tables asks WHICH history rhymes; the Gyre asks whether the basin's dynamics are deterministic enough to rhyme at all. |
| **Rogue Wave** ★ | *(v2.6)* The tail law. Extreme value theory is literally the mathematics of rogue waves: peaks-over-threshold GPD (probability-weighted moments, bootstrap CIs, threshold-sensitivity table printed) on the SAME declustered pop statistic as PROOF. Swell's empirical exceedance curves stop dead at the largest pop in the sample; the GPD is the honest instrument for the wave that is NOT in the sample yet — the once-a-decade pop in bp, P(pop ≥ 25bp within a quarter), and whether the tail is getting heavier as the buffers drain (annual expanding ξ refits). |
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
- **The Estuary** — upstream FX/material pressure minus the funding pressure already
  priced in SOFR and commercial paper. A positive Passage gap says the cash burden is
  running ahead of its usual landing zones; the discovery/holdout map then shows which
  links actually survived unseen history. Monthly physical breadth is never filled
  forward into the daily evidence, and the cash lab never pretends user assumptions
  are observed exposures.
- **The Navigator** — an LLM forecaster made accountable: one committed
  P(funding event, 5bd) per data-day, grounded strictly in the live board,
  written into the hash-chained record. An LLM cannot be honestly backtested
  (it has read the history), so its FORWARD record is its only evidence and
  its weight stays zero until that record earns a hearing. `seiche navigator`.
- **The Communiqué** — FOMC statements read as vintage-stamped data: frozen
  deterministic lexicons score policy direction, balance-sheet bias and
  funding-stress vocabulary per statement; the change vs the previous
  statement is the signal, and the Time Machine replays text as it stood.
- **The TED bridge** — the ML Lab pretrains on the TED spread's 1990–2018
  funding-stress record (2008/2011/2016) in the same feature slots,
  down-weighted, and publishes the transfer gain vs the SOFR-only model
  either way.
- **The Stack + The Book** — the rule index, ML Lab, Tide Tables analogs, the
  Swell curve and Bathymetry's first-passage odds all emit P(funding event, 5bd); the Stack calibrates and blends them
  walk-forward (publishing the equal-weight mean whenever the fitted blend can't
  beat it), publishes member **dispersion** as a first-class ambiguity signal, and
  the Book converts the result into explicit daily paper positions with costs,
  benchmarks and bootstrap CIs. Every view's daily forecast and every position is
  appended to the hash-chained PIT record — a track record no reconstruction can
  polish.
- **Turn Barometer** — forecasts the *next* month/quarter-end turn's severity with
  leave-one-out cross-validation, always benchmarked against a naive forecast. When
  the model can't beat naive, it says so and publishes naive instead.
- **Playbook** — what S&P/VIX/OAS/yields did the last N times the board looked like
  this, in native units, with n printed. Decision support, not advice.
- **PROOF** — the construction-PIT diagnostic lab: the index rebuilt with expanding-window statistics
  only (causal truncation is unit-tested, but source history is final/current-vintage), recall/precision with Wilson 95%
  intervals, run-level precision (alert days are serially correlated; runs are the
  honest trials), episode-by-episode lead times *including the ones it missed* —
  and the **orthogonal signal test**: the same event-capture with the target's own
  variable family (spread/tails) removed from the signal. At build: orthogonal
  recall 69% [CI 42–87] vs 62% full, with the structural components alone at the
  98th–100th percentile 42 days before the Sep/Dec-2025 squeezes. The claim is
  causal structure, not autocorrelation.
- **Time Machine** — final/current-vintage construction-PIT reconstruction using observations dated on or before any date since ~2018; it does not recreate the publication vintage visible then. Reconstructed to
  **Sep 12 2019**, the board reads EROSION with reserves $576B below the kink and
  flags **Sep 16 2019** — the exact day the repo market broke — as a crunch window.

Principles: **no naked numbers** (every value carries source + as-of + staleness),
**fail-loud** (a dead feed shows as DEAD and reduces published coverage — it never
silently vanishes), **honest lags** (COT is T+3 by construction; shown, not hidden),
and **honest scope** (a registered market with no qualifying public benchmark stays
visible as unavailable; a policy rate or restricted input is never relabelled as a
live traded benchmark).

## Public world-market contracts

The **[`Market Atlas`](https://seiche.info/#corpus)** drills from nine live
market packs into canonical instruments, observations, source/publication/
knowledge clocks and rights, then continues into the shared bulk corpus at
**[`/api/v2/corpus`](https://api.seiche.info/api/v2/corpus)**. Full normalized
BIS rows use bounded, generation-bound access. The engine ledger reconciles
1,118 acquisition attempts to 1,110 verified unique objects, including eight
recovered retries; its immutable public index exposes only rights-approved
metadata and structural profiles. BSE, NSE, NY Fed and restricted acquisition
recipes remain explicit metadata-only collections when values or downloads are
not publishable. Complete public BIS flows use immutable manifests and
content-addressed, byte-range-capable gzip shards so large transfers can resume
without mixing generations. Seiche request handlers do not fetch this corpus into an
analytic, and an object's `data_class` or acquisition review never grants
model, training, scoring, execution or redistribution permission. See
[`docs/MARKET_CORPUS.md`](docs/MARKET_CORPUS.md) for endpoints and invariants.

- **`GET /api/v2/world-markets`** returns the versioned
  `seiche.world-markets.v1` projection of an already completed snapshot. It
  connects money-market funding, 22 official daily FX reference series, three
  trade-weighted dollar indexes, and a bounded macro-capital transmission view
  through selected Treasury, credit, volatility, dealer, positioning and
  commodity evidence, plus a signed metadata-only China macro side projection
  over four release-reviewed NBS series identities. The response carries canonical citation URLs, separate
  generation/evidence clocks, and `observed`, `derived`, `structural`,
  `restricted` or `unavailable` status. Add `?section=forex` (or `summary`,
  `money_markets`, `capital_markets`, `china_macro`, `sources`, `methodology`, `all`) for a
  smaller projection. The request never starts collection, scans historical
  repositories or fits a model.
- Crawlable citation pages live at **[`/markets/`](https://seiche.info/markets/)**,
  **[`/markets/forex/`](https://seiche.info/markets/forex/)** and
  **[`/markets/capital-markets/`](https://seiche.info/markets/capital-markets/)**,
  with the signed metadata-only China catalog at
  **[`/markets/china-macro/`](https://seiche.info/markets/china-macro/)**.
  They describe real public coverage and its gaps; they do not claim executable
  quotes, every security or licensed redistribution rights. China raw exports,
  histories and values remain restricted and never enter a gauge or score; see
  [`docs/NBS_SIGNED_EXPORT.md`](docs/NBS_SIGNED_EXPORT.md) for the operator and
  verification contract.

## Public money-market contracts

- **`GET /api/money-markets`** returns the full USD Money Market Desk. Every
  metric carries its source, as-of date, cadence and plain-language explanation;
  meaningful cards add native-series changes, a one-year robust z-score and a
  three-year empirical percentile. Derived spreads use exact common observation
  dates—there is no forward-fill across unrelated source clocks. The headline
  displays both the raw most-extreme channel and a conservative Bonferroni-
  adjusted desk-wide rank; reserve and ON-RRP stock levels remain visible
  context but cannot manufacture a funding-stress label by themselves.
- **`GET /api/v2/money-markets`** returns the Global Money Market Atlas from
  already collected canonical observations for all **11 registered packs**.
  Eleven is a catalog count, not a claim of 11 live benchmarks or validated
  markets. It also returns a **52-row source-audited expansion ledger** with
  benchmark taxonomy, authority link, access/rights caveat, confidence, review
  stage and verification date. Those rows are discovery metadata, not quotes.
  Query coverage, faults and per-market status for what is actually available
  now; the request never starts collection.

Atlas state is explicit. `AVAILABLE` means a redistributable raw observation is
present. `DERIVED_CONTEXT` means a restricted/derived-only input may contribute
non-reversible own-history statistics, while its raw level and history remain
withheld. `POLICY_ONLY` means an official policy anchor is visible but no eligible
traded benchmark is available; policy is not used as a substitute. A declared
unavailable market remains an evidence gap, not a calm reading. Changes and
history windows follow each adapter's **native cadence**—weekly or monthly series
are never padded into daily data—and cross-market comparison uses own-history
normalization rather than unlike rate levels.

Seiche's AGPL-3.0-or-later license covers the code. It does not override upstream terms:
`allowed` inputs may expose values, `derived_only` inputs expose bounded derived
context, `metadata_only` inputs can describe availability but cannot enter public
calculations, and `prohibited` values and source metadata are omitted.

## Run it

```bash
# backend (Python 3.12+)
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
seiche swell              # the funding-stress forward curve, 6 weeks out
seiche physics            # the physics board: floor, modes, determinism, tail law
seiche bathymetry         # the basin floor in detail: potential, spectrum, entropy, first passage
seiche book               # the Book: today's positions + walk-forward P&L verdict
seiche ask "…"            # desk assistant, grounded in the live board
seiche serve              # API + UI
seiche mcp                # serve the board to AI agents over MCP (stdio)
seiche nbs-intake catalog # four code-owned NBS source identities; no values
seiche nbs-intake status  # verify the public-only signed China revision head
```

## For AI agents (MCP)

Seiche is also a [Model Context Protocol](https://modelcontextprotocol.io)
server — any MCP-capable agent (Claude Code, Codex, your own) can read the live
board as tools. Where a data feed hands an agent raw macro numbers, Seiche hands
it the conclusion: a regime read, forward event odds, historical analogs, and a
historical diagnostic whose status, misses, and eligibility flags stay attached.
Stdlib-only, no new dependencies.

```bash
claude mcp add seiche -- seiche-mcp          # Claude Code, local (stdio)
SEICHE_MCP_PUBLIC=1 seiche-mcp               # free surface only
```

Or, zero-install, over HTTP: the same tools are served at **`/mcp`** on the API
(`https://api.seiche.info/mcp`). Add the URL and start calling. Twelve tools
answer anonymously, no token, no sign-up, no email:

```bash
claude mcp add --transport http seiche https://api.seiche.info/mcp
curl https://api.seiche.info/api/gauge
```

The copy-paste quickstart and live tool runner are at
**[seiche.info/developers](https://seiche.info/developers)**.

| tool | what it answers |
|---|---|
| `latest_article` | today's exact full-text editorial with its evidence clock and publication receipt |
| `funding_stress_now` | the live composite, the regime, the decomposition, the Tell |
| `trade_safety_risk_context` | deterministic cache-only regime/index/coverage/clocks for order guards; context-only and never execution authority |
| `historical_analogs` | the closest days in the record, and what followed them |
| `proof_backtest` | the track record with its misses and its confidence intervals |
| `data_health` | freshness and provenance for every input, before you trust a reading |
| `crypto_stress_record` | labelled crypto episodes replayed against the funding board |
| `institutional_flows` | who is positioned where, from public prints |
| `money_market_context` | compact, chartless USD desk summary or one requested section, plus sources/methodology selectors |
| `world_markets_context` | bounded summary or money, forex, capital, source and methodology sections with canonical citation URLs |
| `oil_funding_context` | observed oil/funding and Ballast evidence, live-vs-reference market structure, plus clearly separated scenarios |
| `fx_materials_passage` | upstream FX/material pressure and the Passage's holdout-tested links |

That is the conclusion, the precedent, the honest record, the freshness, granular
USD money-market context, unified world-market context and cross-market transmission context, and it stays
free. Five analysis tools want a bearer token because they read gated
forecasting, replay, positioning, prose, or LLM engines rather than a published
contextual conclusion:
`funding_stress_forecast`, `replay_asof`, `positioning_book`, `desk_brief` and
`ask_desk`. `institutional_flows` answers anonymously but keeps its
`method_versions` back for the same reason.

An authenticated hosted caller also sees five private **Agent Room** preview
tools: `agent_room_register_key`, `agent_room_create`,
`agent_room_append_event`, `agent_room_list_events`, and `agent_room_verify`.
They record client-signed, server-co-signed agent discussion in a tamper-evident
room; every record is non-executable and grants no acceptance, order, execution,
payment, settlement, or custody authority. The full bearer-authenticated hosted
catalog is therefore 22 tools (12 public evidence + five gated analysis + five
Agent Room). See [the exact security and signing contract](docs/AGENT-ROOM.md).

Nothing fails at call time over this: `tools/list` returns exactly the tools the
caller can run, so an agent never sees a tool it cannot use. Both surfaces are
rate-limited per caller and metered per UTC day (anonymous callers per IP).

Full setup, the tool catalogue, client config, metering, tokens, and the
pay-per-call option for the five analysis tools: **[docs/MCP.md](docs/MCP.md)**.

Want the board in your pocket instead of a client config? The
**[Hermes desk-agent kit](integrations/hermes/)** turns
[hermes-agent](https://github.com/NousResearch/hermes-agent) into a Seiche desk
agent on Telegram/Discord/Slack: a scheduled morning brief, regime alerts with
anti-noise rules, construction-PIT episode replays, and a PROOF-grounded answer to
"can I trust this". Walkthrough: **[docs/HERMES.md](docs/HERMES.md)**.

Where this is heading on the crypto side (stablecoin reserves, tokenized
Treasuries, DeFi rates all sit on the market Seiche reads): **[docs/CRYPTO.md](docs/CRYPTO.md)**.

Alerts dedupe per state in SQLite, notify via macOS notification and optional
webhook (`SEICHE_WEBHOOK_URL` — Slack/Telegram/ntfy style `{"text": ...}`).
A launchd template lives in `ops/com.seiche.watch.plist`.

## Production deployment

```bash
# Existing canonical host: install or refresh the signed release controller
# without activating it, then run its gate-only acceptance cycle.
bash /home/seiche/app/ops/deploy/install-release-poller.sh
SEICHE_CONTROL_GATE_ONLY=1 /usr/local/sbin/seiche-release-poll
```

Production uses the operator-provisioned `/home/seiche/app` layout documented
in `ops/deploy/RELEASE-POLLER.md`; this repository intentionally has no
unattended first-VPS bootstrap. The old `/opt` installer is retired because its
service and state layout never matched production.

`GET /api/health` never starts or waits for the full board build. It returns
the last completed snapshot's health fields with HTTP 200, or an immediate
HTTP 503 with `status: warming_or_unavailable` while the cache is cold. The
production background warmer owns snapshot construction; deployment probes
only observe whether it has published a result.

Put a TLS reverse proxy in front. The box already serving another site
(e.g. Palimpsest) just adds a vhost — nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name seiche.example.com;   # certbot --nginx -d seiche.example.com
    location / { proxy_pass http://127.0.0.1:8787; proxy_set_header Host $host; }
}
```

**Signed releases**: the on-host release poller is the sole automatic
controller. It accepts only the reviewed SSH-signed `main` tip, tests that exact
SHA in isolation, and hands it to the rollback-owning deploy wrapper. The
`deploy-hetzner` workflow is `workflow_dispatch`-only break-glass recovery and
must not run while the poller is active. The API cache lives under
`/home/seiche/app/backend/data`; canonical market state and backup receipts live
under `/var/lib/seiche` and `/var/backups/seiche-market`. Follow the poller and
market backup runbooks rather than copying one directory as if it were the
whole track record.
LLM keys for the desk assistant/Navigator and Telegram alert credentials belong
in a root-owned production environment file, not the retired
`ops/deploy/seiche.service` template. On the host, create
`/etc/seiche/operator.env` with mode `0600`, then attach it to the actual
`seiche-api.service` through a non-secret systemd drop-in:

```bash
sudo install -d -o root -g root -m 0755 /etc/seiche
sudo test -e /etc/seiche/operator.env || \
  sudo install -o root -g root -m 0600 /dev/null /etc/seiche/operator.env
sudoedit /etc/seiche/operator.env
sudo install -d -o root -g root -m 0755 \
  /etc/systemd/system/seiche-api.service.d
printf '%s\n' '[Service]' \
  'EnvironmentFile=/etc/seiche/operator.env' | \
  sudo tee /etc/systemd/system/seiche-api.service.d/operator-env.conf >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart seiche-api.service
```

Use systemd `KEY=value` syntax in the environment file for only the required
`SEICHE_LLM_*`, `SEICHE_TELEGRAM_*`, or webhook settings. Never commit their
values. Confirm permissions with `stat`, then check `systemctl status
seiche-api.service` and the API health route after restart.

## Tuning the editorial voice

`backend/seiche/config.py` quarantines every judgment call: composite weights, regime
thresholds, resonance/turn/tell parameters, alert rules, the episode library, contract
DV01s. The math never hides an opinion.

## Non-goals

The twelve-tool public evidence surface needs no account and does not depend on
paid upstream data; optional licensed or tenant inputs remain explicitly bounded.
Five compute-heavy forecast, replay, positioning, prose and LLM tools are
account-gated. Seiche does not claim intraday-tick coverage: daily cadence plus
operation results is the honest granularity of the public stack. Historical
reconstruction uses final/current-vintage inputs and is therefore
**construction-PIT, not validated-backtest evidence**. The engine blocks the
historical backtest output unless every required input has an ALFRED/as-published
vintage manifest. From v2 onward Seiche accrues a true as-published point-in-time
record (`/api/pit`) and stores immutable observation captures for forward vintage
reconstruction. Those forward captures cannot repair vintages that were never
retained. Not investment advice.
