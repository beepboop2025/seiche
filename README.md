# SEICHE

[![sealed record](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.seiche.info%2Fapi%2Fbadge%2Frecord)](https://api.seiche.info/api/notary)

> A **seiche** is a standing wave in an enclosed body of water — invisible from the
> shore, until it sloshes over the edge. Funding stress behaves the same way.

**Seiche is a free, open source (AGPL-3.0) funding-stress, positioning and
divergence terminal** for the dollar
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

## v2 "Deep Water" — twenty-two engines, eight analytics layers, twelve tabs

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
| **Echo Engine** | Does today's 30-day trajectory rhyme with the run-up to any historical stress episode? |
| **Tide Tables** ★ | What happened next, every time the water looked like this? Markets rhyme, so forecast like a tide table: the k nearest analogs of today's trailing state trajectory over ALL history (labeled or not, expanding-z — no look-ahead) publish their actual forward spread paths as a fan, the share followed by a funding event within 5bd (Wilson CI vs climatology), a NOVELTY gauge ("the board has never looked like this" is its own signal, and flags the fan as extrapolation), and a walk-forward hindcast that says honestly whether analogs beat the base rate. |
| **RV X-Ray** | How big is the leveraged Treasury RV complex, and what does a 5/15/30bp shock do to it? |
| **Crowding** | Where are leveraged funds most crowded relative to their own history (UST curve, SOFR/FF futures, S&P)? |
| **Auction Digestion** | Is the market choking on Treasury supply? |
| **Warehouse** | How full is the primary-dealer balance sheet — the shock absorber of last resort? (NY Fed PD stats by maturity bucket) |
| **Resonance Engine** ★ | *The seiche made literal:* does the same calendar forcing (month-end, quarter-end, year-end, tax dates) produce a bigger slosh than it used to? Amplification = damping loss = fragility rising while levels look calm. |
| **Undertow** ★ | The free-decay half of the resonance physics: critical slowing down (Scheffer et al.), measured continuously. Rising lag-1 autocorrelation + variance of the detrended spread/tail and a stretching recovery half-life after everyday pops = the basin losing damping on days when NOTHING is happening. Expanding percentiles only; weighted into the composite as structural evidence. |
| **Swell Forecast** ★ | The funding-stress **forward curve** — a term structure nobody publishes, not even the $32k terminals: P(SOFR−IORB pop ≥ 2/5/10/20bp) for each of the next 42 business days, built from the PUBLIC forcing calendar (turn/tax/settlement days each keep their full expanding distribution of historical pops — small severities lend the rare big ones statistical mass), lifted by the live damping state and announced coupon settlements. Compounds to P(event by horizon), walk-forward validated vs climatology with the reliability table printed, and the verdict self-demotes to "trust the dates, not the levels" when the levels stop earning it. |
| **Hydrophone Array** ★ | How connected is the plumbing right now? (absorption ratio over 11 funding series + a live lead-lag map of which pipe is upstream) |
| **Global Basin Coupling** ★ | Are the US, euro-area, UK, India (FX channel) and crypto basins moving as one tide? Plus the global confession channel: USD swap-line draws (test operations excluded). |
| **Stablecoin Moorings** ★ | The offshore-dollar basin's tie lines: peg deviations (USDT history + live board), total-circulation flows ($200B+ of T-bills behind them), and the 24/7 BTC canary — crypto trades when funding markets sleep. |
| **ML Lab** | Learned P(funding event within 5bd): walk-forward with a 5bd boundary embargo, benchmarked against climatology AND the rule-based index, reliability table + decision-utility scoring published. Verdict at build: ranks better than the rule (OOS AUROC 0.826 vs 0.806; 0.812 on the orthogonal feature set) but probability levels don't beat climatology — use for ranking/alerting, not literal odds. The verdict self-updates. |
| **Station-Keeping** ★ | Orbit-determination transfer: propagate the reserve system's expected state (fiscal seasonal, calendar buckets, trailing drift), CUSUM the innovation residuals, flag unmodeled "burns" — debt-ceiling cash games, RMP pace changes — often before they're narrated. Doubles as the Weather model's health monitor. |
| **Riptide** ★ | The pop prognosis — the one morning the whole desk asks the same question, answered: *chop or current?* Every declustered spread pop becomes a trial; the discriminators (RRP co-sign — a pop WITHOUT its mechanical quarter-end co-move is genuine scarcity, the 2025 signature; calendar bucket; damping state) feed a deliberately tiny walk-forward logistic that classifies the live pop as calendar mechanics or the start of a squeeze, with P(sticky) and P(escalates) validated pop-by-pop against the base rate. Speaks only when there is a live pop; flat water is itself the reading. |
| **The Breakwater** ★ | The rescuer modeled — the feature no forecaster ships: the Fed is not weather, it is a PLAYER, and every intervention in the public record is a confession of where its pain threshold sat that day. A zero-parameter revealed-preference catalog (repo ops '19, QE '20, SRF '21, BTFP '23, QT taper '24, RMPs '25) replayed against the board as of the day before each announcement yields the revealed threshold and a live **rescue proximity** gauge — which cuts both ways, and the engine says so: a forecast miss after an intervention is a save, not a false alarm. |
| **Bathymetry** ★ | The basin floor mapped from the water's motion — the physics program end to end. The daily pop statistic is treated as a diffusion and its dynamics are RECONSTRUCTED from the data (Kramers–Moyal / empirical Langevin): drift → the **effective potential** (the well the spread rests in, its restoring stiffness, and the escape barrier printed in units of thermal energy k_BT); the binned transition operator → the **quantum-dual energy spectrum** (Fokker–Planck ↔ Schrödinger: stationary density = ground state, eigenvalue moduli = energy levels, spectral gap = inverse of the slowest relaxation time — critical slowing down measured operator-theoretically, corroborating Undertow by an independent estimator); stationary probability currents → **entropy production** (Schnakenberg, nats/day — the arrow of time: a calm basin relaxes, a stressed one is pumped); and absorbing-boundary **first passage** → P(funding event within h bd | today's state) and the expected business days to the next event, Kramers' escape problem solved exactly on the measured landscape, no simulation. Expanding counts only, walk-forward validated vs climatology, and the daily probability joins the Stack as its own member with its own record. |
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
seiche swell              # the funding-stress forward curve, 6 weeks out
seiche physics            # the physics board: floor, modes, determinism, tail law
seiche bathymetry         # the basin floor in detail: potential, spectrum, entropy, first passage
seiche book               # the Book: today's positions + walk-forward P&L verdict
seiche ask "…"            # desk assistant, grounded in the live board
seiche serve              # API + UI
seiche mcp                # serve the board to AI agents over MCP (stdio)
```

## For AI agents (MCP)

Seiche is also a [Model Context Protocol](https://modelcontextprotocol.io)
server — any MCP-capable agent (Claude Code, Codex, your own) can read the live
board as tools. Where a data feed hands an agent raw macro numbers, Seiche hands
it the conclusion: a regime read, forward event odds, historical analogs, and an
honest backtest. Stdlib-only, no new dependencies.

```bash
claude mcp add seiche -- seiche-mcp          # Claude Code, local (stdio)
SEICHE_MCP_PUBLIC=1 seiche-mcp               # free surface only
```

Or, zero-install, over HTTP: the same tools are served at **`/mcp`** on the API
(`https://api.seiche.info/mcp`) — the full tool surface, free for everyone,
no token needed (rate-limited per caller so one client can't crowd out the rest).

Full setup, the tool catalogue, client config, and metering: **[docs/MCP.md](docs/MCP.md)**.

Want the board in your pocket instead of a client config? The
**[Hermes desk-agent kit](integrations/hermes/)** turns
[hermes-agent](https://github.com/NousResearch/hermes-agent) into a Seiche desk
agent on Telegram/Discord/Slack: a scheduled morning brief, regime alerts with
anti-noise rules, point-in-time episode replays, and a PROOF-grounded answer to
"can I trust this". Walkthrough: **[docs/HERMES.md](docs/HERMES.md)**.

Where this is heading on the crypto side (stablecoin reserves, tokenized
Treasuries, DeFi rates all sit on the market Seiche reads): **[docs/CRYPTO.md](docs/CRYPTO.md)**.

Alerts dedupe per state in SQLite, notify via macOS notification and optional
webhook (`SEICHE_WEBHOOK_URL` — Slack/Telegram/ntfy style `{"text": ...}`).
A launchd template lives in `ops/com.seiche.watch.plist`.

## Deploying on a VPS (Hetzner etc.)

```bash
# first time, as root — clones to /opt/seiche, runs the test gate, builds,
# installs systemd units (API on 127.0.0.1:8787 + a 30-min alert timer)
bash ops/deploy/install.sh

# every release after that: push to main — the deploy-hetzner workflow
# runs the box's forced-command chain (test gate, rollback, restart,
# warm-up-aware health check). Manual equivalent, as root on the box:
bash /home/seiche/app/ops/deploy/update.sh   # engine deploy + Caddyfile
```

Put a TLS reverse proxy in front. The box already serving another site
(e.g. Palimpsest) just adds a vhost — nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name seiche.example.com;   # certbot --nginx -d seiche.example.com
    location / { proxy_pass http://127.0.0.1:8787; proxy_set_header Host $host; }
}
```

**Auto-deploy on every merge to main**: add two repo secrets —
`HETZNER_HOST` (the server IP) and `HETZNER_SSH_KEY` (a private key whose
public half is in root's `authorized_keys`) — and
`.github/workflows/deploy-hetzner.yml` runs the test-gated `update.sh` on the
box after each push to main. Without the secrets it skips cleanly.
The SQLite cache AND the PIT/Navigator as-published record live in
`/opt/seiche/backend/data` — back that directory up; it is the track record.
LLM keys for the desk assistant/Navigator and Telegram alert credentials go
in the `[Service]` environment of `ops/deploy/seiche.service`.

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
