# Repo intake — July 2026 (gs-quant, qlib, FinceptTerminal, awesome-quant, ML4T)

Five repos mined against Seiche's problems on 2026-07-10 (companion to
RESEARCH-2026-07.md, the paper sweep). Same bar: free keyless data, no
look-ahead, fail-loud, self-demoting vs the honest naive baseline. Ranked by
(value to the thesis) / (build cost).

## Tier 1 — adopt next (high value, low cost, on-thesis)

1. **DBnomics collector** (found via FinceptTerminal's catalog). One keyless
   REST API (`api.db.nomics.world`) federating BIS / IMF / OECD / ECB /
   national central banks. Unlocks the global-plumbing layer the Basins
   engine can't reach today: BIS global liquidity (WS_GLI), credit-to-GDP
   gaps (WS_TC), cross-border banking claims (WS_XBS), and a China
   money-market path (SHIBOR/DR007/OMO) without scraping SDMX. One new
   `sources/dbnomics.py` in the existing Series/provenance envelope.
2. **Model Confidence Set / Reality Check engine pruning** (`arch.bootstrap`:
   Hansen SPA, White RC, MCS; + ML4T ch.7's BH-FDR/Deflated-Sharpe framing).
   With ~15 engines there is a data-snooping objection PROOF's per-engine
   permutation null doesn't answer: which engines are statistically
   indistinguishable from the best? Feed each engine's OOS loss series into
   MCS and publish the surviving set — "which engines earn their place",
   snoop-corrected. Extends the honesty brand directly. New dep: `arch`
   (Sheppard, first-rate maintenance).
3. **Adaptive conformal intervals on the Stack** (MAPIE v1.3+, EnbPI/ACI).
   Venn–Abers calibrates the probability; ACI adds coverage-guaranteed
   intervals that hold under regime drift, no look-ahead. Publish "P(event)
   0.31 [coverage-guaranteed 90% set]" next to the existing bands.
4. **Markov-switching regime posterior** (statsmodels
   `tsa.regime_switching`). A 2–3 state Hamilton-filter model on the
   spread/tail gives a FILTERED (point-in-time, never smoothed) P(stress
   regime) — a principled discrete-regime gauge next to the composite's
   editorial thresholds, and a candidate ML feature. Must beat climatology
   walk-forward or self-demote, as usual.

## Tier 2 — architecture steals (designs, not dependencies)

5. **qlib's PIT schema** for the as-published record: one append-only row per
   publication event `(release_date, ref_period, value, supersedes_id)` with
   as-of reads by `release_date <= as_of`. Cleaner than snapshot blobs for
   revision-carrying series (H.4.1, OFR restatements): O(revisions) space,
   explicit period≠release-date separation, and the truncation-equality
   suite gets a direct invariant ("an as-of read never returns a row
   published later"). Adopt when a revision-sensitive engine needs it — the
   pit:* blobs stay as the board-level record.
6. **fit/transform processor contract + generalized truncation test**
   (qlib's `fit_start_time` discipline). Every stateful transform (z-score,
   winsor bounds, PCA loadings) exposes fit(train_slice)/transform(); one
   generic test asserts pre-split output is identical whether or not
   post-split rows exist. This is the Leak Audit's NORM_GLOBAL toggle turned
   into a preventive interface — same leak class, caught at construction.
7. **gs-quant interface steals** (Apache-2.0, keyless parts only):
   `Window(size, ramp)` — warm-up as a type, so no engine can emit off a
   4-obs window of a 60d estimator; `align(method=intersect|step|...)` for
   spread construction (two legs, different calendars — per-spread explicit
   choice instead of silent pandas alignment); the Trigger/Requirements
   boolean-composition layer for ALERT_RULES ("tail_z>2 AND rrp falling AND
   settlement within 5bd" as AggregateTrigger(ALL_OF,...) instead of new
   ad-hoc rule code); the scenario grammar (partial selector + typed shock:
   ABSOLUTE|PROPORTIONAL|OVERRIDE|REPLAY) to unify the three scenario
   engines under one composable object. Everything else in gs-quant is
   Marquee-entitlement-gated — dead-end, mapped in the agent notes.
8. **Append-only forecast/label updater shape** (qlib PredUpdater/
   LabelUpdater): the PIT ledger already appends forecasts; formalize the
   second write path — a resolve-labels job that backfills the realized
   5bd outcome once the window closes, so calibration always scores
   as-published forecasts against outcomes unknown at forecast time.

## Tier 3 — new engines (bigger builds, gate hard)

9. **PCMCI+ directed lead-lag network** (tigramite v5.2) — the
   highest-conviction net-new capability, surfaced independently by both
   catalogs. Upgrades Hydrophone's symmetric correlation edges to a
   DIRECTED, lagged causal graph; the alert is the parent set of the stress
   target CHANGING (offshore→onshore direction strengthening = the 2019 /
   Mar-2020 signature). This is IDEAS.md #10 (Basin Tomography) with a
   better instrument than transfer entropy (IDTxl rejected: data-hungry,
   Java/OpenCL backend). Edges published as hypotheses with conservative
   alpha, n≈2000 stated.
10. **ruptures (PELT) break-density gauge** — panel-wide multiple-changepoint
    dating beyond Station-Keeping's per-channel CUSUM; break density as a
    fragility feature. Cheap, offline, maintained (v1.1.10, 2025).
11. **BSTS counterfactual for Station-Keeping events** (CausalImpact-style):
    when a maneuver is flagged (TGA burn, QT step, facility launch), publish
    the event's stress impact with credible intervals — turns burn alarms
    into attributed, uncertainty-bounded impact statements.
12. **HawkesEM cross-check for Microseism** (tick, benchmark-only dep):
    non-parametric kernel estimate on the same shock catalog; divergence
    from the exponential kernel flags misspecification. Belongs in tests /
    an offline study, not the board.
13. **Exponential-forgetting sample weights in the ML Lab** (DDG-DA stripped
    to its cheapest honest baseline, GF-Exp): one-line trainer change,
    half-life chosen walk-forward, kept only if it beats uniform weights
    out of sample.

## Product / UX (from FinceptTerminal, mostly by contrast)

14. **Command palette** (⌘K fuzzy-jump over tabs + series mnemonics +
    "explain this engine") — Fincept, despite the branding, ships no
    keyboard-driven terminal UX; this is the cheapest way to LOOK like a
    terminal and is a few hundred lines of React.
15. **Named analyst lenses over the existing MCP server** (Repo Desk / Fed
    Plumbing Watcher / Crisis Historian): stored prompts + whitelisted tool
    subsets — the 37-persona idea shrunk to one domain, zero new infra.
    Pair with bring-your-own-key LLM config (OpenRouter/Ollama) so
    inference cost stays with the subscriber.
16. **Positioning lesson:** Fincept (100+ connectors, 16 brokers, 37 agents)
    is publicly funding-constrained and pivoting paid-private. Breadth is
    unmaintainable solo; Seiche's one-domain depth is the moat. Their
    AGPL + commercial dual license is worth considering if institutions
    ever touch the record (business decision, not code).

## Ruthless rejects (so nobody re-litigates)
- qlib model zoo / Alpha158 / cross-sectional processors / MongoDB task
  manager — equity-alpha and cluster machinery, wrong shape.
- qlib "monitoring" — updates predictions, does not detect degradation;
  Seiche's Station-Keeping is already ahead. qlib has NO calibration layer.
- gs-quant everything entitlement-gated (measures_*, PricingContext, risk,
  markets/core) — requires a GS client id Seiche will never have.
- hmmlearn (limited-maintenance; statsmodels covers it), IDTxl (data-hungry
  + Java), signatory (abandoned), tsfresh feature farms (multiple-testing
  minefield vs the no-lookahead discipline), River (streaming, wrong shape),
  pyEDM (Gyre already covers EDM).
- Fincept's ReactFlow node editor — overkill for one domain.
