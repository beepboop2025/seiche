# Seiche Research

Seiche is a free, open source terminal for funding stress in the US money market, built
entirely from public data (Fed H.4.1, NY Fed operations, OFR repo, Treasury cash). This
page indexes the research the terminal publishes about itself: its sealed forecast
record, its running studies, and the methods behind them. Everything here is checkable
at the code level in this repository.

The sibling product LiquiLens watches institutions the way Seiche watches the plumbing.
The combined research index lives at https://liquilens.in/research/.

## The sealed forecast record (PROOF)

The terminal publishes forward event odds on funding stress and then keeps the score,
hits and misses both. Entries are hash chained, signed, and anchored externally
(OpenTimestamps) so the as published history cannot be quietly rewritten. A live badge
reports the record's state, and a dead man alert fires if the record develops holes.

- Scoreboard: https://seiche.info/#proof
- Verification: the notary proof endpoints are served by the public API, and the
  anchoring code is in this repository.

Why this exists: an early warning product with no public track record is asking for
faith. We would rather be graded.

## The daily dispatches: plumbing leads price

Every day the terminal writes a short letter on what the plumbing did. The archive is
accumulating a running study: documented episodes where funding stress appeared in
operations and money market prints before it appeared in price. Each dispatch is a
static, linkable page with the data attached, published before the outcome resolves.

- Archive: linked from https://seiche.info

## The crypto record (Wrecks)

Funding stress episodes from crypto markets, scored against what actually happened,
served on the free page and the PROOF tab. Same discipline as the fiat record: episodes
are stated, then graded.

## Money-market research: the USD desk and global atlas

The [USD Money Market Desk](https://api.seiche.info/api/money-markets) is a
descriptive ledger across seven sections: policy anchors and overnight spreads;
SOFR/TGCR/BGCR distributions and tails; repo-segment rates and volumes;
commercial-paper spreads; bills; official liquidity buffers and facilities; and
money-market-fund repo plumbing. Cross-source formulas use exact common dates and
never forward-fill one publisher's clock into another. Changes respect the
series' native cadence, while robust z-scores and percentiles compare each metric
only with its own trailing history. Its worst-of regime is context, not a causal
model, event probability or trading signal.

The [Global Money Market Atlas](https://api.seiche.info/api/v2/money-markets)
currently registers 11 monetary-area packs: US, euro area, UK, Japan, China,
Hong Kong, India, South Korea, Australia, New Zealand and Singapore. Registration
declares semantics, clocks, instruments and access policy; it does **not** claim
that every pack has a live public benchmark or has passed validation. Query the
live response for current coverage. A market can expose an `AVAILABLE` raw
benchmark, `DERIVED_CONTEXT` from a restricted input with its level/history
withheld, `POLICY_ONLY` when only an official anchor is eligible, or remain
declared unavailable. Missing evidence is not scored as calm, and policy rates
are not substituted for traded cash benchmarks.

The same contract carries a source-audited discovery ledger for 52 additional
monetary areas. Each row identifies the benchmark or policy proxy, its economic
type, official authority and URL, rights/access review, confidence, integration
stage and 2026-08-21 verification date. Ledger rows contain no market values and
must pass methodology, bitemporal, calendar, source, legal and operational gates
before becoming a canonical pack.

The Atlas keeps local units, calendars and publication frequency. Weekly and
monthly observations stay weekly and monthly; own-history windows use the
adapter's native observation count, and unlike cross-currency rate levels are
never averaged into a global score. AGPL-3.0 covers the implementation, not the
upstream data: `allowed` observations may be shown, `derived_only` inputs may
produce non-reversible context, `metadata_only` inputs cannot enter public
calculations, and `prohibited` values and source metadata stay out of the public
projection.

## Methods

The forecasting stack prefers methods with guarantees, and each is cited to its source
in the code:

- Conformal prediction for distribution free interval coverage, with coverage
  accounting done per regime rather than on average.
- Expert aggregation across interval forecasters (AgACI style).
- A calendar gated Hawkes process over the shock catalog.
- Regime detection via hidden Markov models, plus Markov, OU with jumps, and Monte
  Carlo scenario engines anchored to the live board.
- Threshold free AUROC with permutation null significance for backtest claims.
- A one switch leakage audit protocol, run against our own pipeline.
- Exact-date intersections for cross-source money-market spreads, with no
  forward-fill across unrelated publication clocks.
- Native-cadence own-history normalization for the global atlas, with no
  upsampling and no comparison of unlike raw rate levels across currencies.
- The stated competence boundary: the backtest distinguishes endogenous funding stress,
  which the board can anticipate, from exogenous shocks, which it can only react to.

## What Seiche does not do

- No paywall on the public evidence terminal: eleven contextual tools remain
  anonymous and free. Compute-heavy forecast, replay, positioning, prose and LLM
  tools may require an account so their operator cost does not narrow the public
  surface.
- No advice. Readings are descriptive states of the plumbing, not trade signals.
- No restricted raw values in the public output. Licensed and tenant adapters
  may be declared, but their absence or redistribution boundary stays visible;
  if a feed goes dark the board says so rather than rendering absence as calm.
- No claim to replace a licensed news/data terminal, real-time quote feed,
  entitlement system, or execution venue. The Atlas intentionally withholds raw
  values when redistribution terms require it.

## License

AGPL-3.0, like everything else in this repository. Read it, run it, attack it.
