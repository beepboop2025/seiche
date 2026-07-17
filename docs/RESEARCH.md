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

## Harbors: five money markets, one tide

The terminal watches five money markets (India, China, the euro area, Japan, Korea) from
each market's own public prints, with Japan on daily data, and estimates directional
spillover between them (Diebold and Yilmaz connectedness) so a reader can see which
market is exporting stress. A dedicated view puts New York and Mumbai side by side.

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
- The stated competence boundary: the backtest distinguishes endogenous funding stress,
  which the board can anticipate, from exogenous shocks, which it can only react to.

## What Seiche does not do

- No paywall, ever. The terminal is a public good; support is voluntary.
- No advice. Readings are descriptive states of the plumbing, not trade signals.
- No private data. If a feed goes dark the board says the feed is dark rather than
  rendering absence as calm.

## License

AGPL-3.0, like everything else in this repository. Read it, run it, attack it.
