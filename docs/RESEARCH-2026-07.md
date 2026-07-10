# Research intake — July 2026 paper sweep

Six papers from the 2026-07-10 alphaXiv run were read against Seiche's
problems. Two became engines on this branch, two became documented
conclusions, two were read-and-discarded with reasons. The bar for adoption
is the house bar: free data, no look-ahead, fail-loud, benchmarked against
the honest naive baseline.

## Adopted

### Microseism (engines/microseism.py) — arXiv:2606.15755
Zelvyte & Griffin, "A Multiplex Network Hawkes Model for Systemic Risk
Measurement" (2026). Taken: the Hawkes intensity decomposition and the
branching structure's spectral radius as distance-to-criticality (for one
stream, the branching ratio n itself). Deliberately discarded: the multiplex
covariate layers and the Bayesian sampler (unidentifiable at our event
counts — the paper's own low-count edges collapse to their priors), and the
logistic-normal kernel (exponential keeps the likelihood O(N)). Their
headline GOF failure — a constant background rate absorbing regime drift
into fake self-excitation — is the reason Microseism's baseline is
calendar-gated (Swell's classify_days buckets) and the null it must beat is
a calendar-Poisson, in-sample (LR test) and walk-forward (Brier/AUROC).
This also resolves IDEAS.md #13's identification question
(Filimonov–Sornette vs Hardiman–Bouchaud) with the calendar gate.

First real-data read (2026-07-10, catalog ≥2bp, n=357 shocks): branching
0.58 (LR p≈0 vs the calendar null), aftershock half-life ~8bd, and the
branching HISTORY is the story — ~0.12 in 2020 rising to ~0.58 by 2026: the
basin became ~5x more self-exciting as the RRP cushion drained. Walk-forward
beats calendar climatology (Brier 0.238 vs 0.287, AUROC 0.63 vs 0.54), so
the gauge earned predictive status rather than self-demoting.

### Leak Audit (engines/leakaudit.py) — arXiv:2605.23959 + 2601.13770 + 2603.20319
The routed paper (Benhenda's Look-Ahead-Bench, 2601.13770) is LLM-specific —
its mechanism is pretraining memorization, which Seiche does not have. The
transferable protocol lives in the sibling it led to: "When Alpha
Disappears" (2605.23959), the one-switch design — hold everything fixed,
break exactly ONE evaluation convention, and the metric delta is the leak.
Their empirical headline: leakage is SELECTIVE — forward-reaching feature
constructors and timestamp misalignment dominate (+15..26 Sharpe-equivalents);
global-sample normalization is usually near-zero. The audit implements
NORM_GLOBAL / TEMP_CENTER / THRESH_FIT toggles against the lite index,
scored on PROOF's own events, and publishes the Leakage Gain of each — the
gains the pipeline refuses to claim, as a table a skeptic can read.

From the implementation-risk paper (Yin et al., 2603.20319 — five backtest
engines diverging up to 3.7%/yr on identical inputs, every divergence a
bug): the determinism check. The clean build runs twice and must hash
identically; the sha256 prints on the board (notary-compatible).

## Documented conclusions (no code change)

### Lead-time compression in AI-mediated markets — arXiv:2602.15066
Ruano & Rajan's LLM-depositor ABM is a phase-transition result, not a speed
measurement: cross-bank contagion jumps discontinuously above an
information-spillover rate of ~0.10 (85%±37% failure at the Twitter-
calibrated 0.30), and the within-episode cascade is a deliberately
frictionless worst case (agents see true fundamentals). CONCLUSION: do NOT
haircut Seiche's historical lead times by a flat factor from this paper. The
defensible inference is regime-dependent — in a high-spillover information
environment the lead-time DISTRIBUTION goes bimodal and the tail
compresses, while the median likely holds. If a live cross-entity
narrative-transmission measure ever joins the board (a rolling
distributed-lag spillover coefficient on public mention volume is their one
implementable construct), it belongs as a lead-time-confidence flag, not an
index input.

### Systemic Risk Radar — arXiv:2512.17185
Read for its multi-layer fragility framing. Verdict: weaker than the title —
the implemented system is a correlation-layer GNN with no closed-form
fragility index, erratic early-warning results (COVID AUROC 0.000, FPR 1.0
in two of three crises), and no lead-time numbers. Nothing to adopt that
Hydrophone (absorption ratio) and Basin Tomography (IDEAS.md #10) don't
already cover better. Kept as a reminder that "graph + GNN" is not by itself
an early-warning method.

## Read for grounding
- arXiv:2502.14551 (Caccioli, "Understanding Financial Contagion" survey) —
  shared background citation for contagion mechanics.
- arXiv:2601.13770 (Look-Ahead-Bench) — its dual regime-matched-window smoke
  test and baseline-band idea (calibrate "acceptable drift" from trivial
  strategies) are noted as cheap future additions to PROOF if an LLM member
  (the Navigator) ever earns weight.
