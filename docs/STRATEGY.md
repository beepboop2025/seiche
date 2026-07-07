# Where Seiche can win — and where it honestly can't

The ambition on the table: "overtake Bloomberg and Jane Street." Ambition is
fuel, but a tool that lies to itself about its opponents will lose to both.
This document states the game precisely, because Seiche's entire brand is
that it does not flatter itself.

## What is NOT beatable (and why pretending otherwise would kill the product)

**Bloomberg's actual moat** is not analytics — it's data breadth (350k users
× every asset class × real-time), entitlements, and the chat network effect.
Seiche runs ~40 free daily series. It will never out-Bloomberg Bloomberg, and
every feature built pretending otherwise is wasted.

**Jane Street's actual moat** is not prediction — it's execution
infrastructure, private order flow, balance sheet, and thousands of
uncorrelated small edges compounded intraday. Seiche sees daily closes with
official lags. It cannot trade against them and should never claim to.

## The game that IS winnable

Both giants have the same blind spot: **neither publishes a falsifiable,
auditable opinion.** Bloomberg sells data with no verdict; a prop desk's
verdicts are secret. Seiche's winnable game is the narrow lane between them:

1. **Own one target completely.** P(funding event within 5bd) on the dollar
   plumbing — a target the incumbents don't publish on, driven by free public
   data the incumbents don't synthesize. Twelve engines feed three
   independent forecasters (rule index, ML Lab, Tide Tables analogs); the
   Stack blends them walk-forward and confesses when the blend adds nothing.

2. **Convert opinion into positions.** The Book (HELM tab) maps the ensemble
   through a frozen rulebook into explicit daily weights and runs the
   walk-forward P&L with costs, benchmarks, block-bootstrap Sharpe CIs and
   per-episode attribution. If it doesn't beat a static mix after costs, the
   page says so in bold. A paper book, stated as such — but a *complete,
   recomputable* one.

3. **Make the track record tamper-evident.** Every day's as-published
   positions are hash-chained (`publisher.py`) and shipped inside the
   published static site; the site repo's git history is the append-only
   backbone. Nobody — including the operator — can quietly rewrite a bad
   month. This is the credibility instrument no incumbent offers at any
   price: Bloomberg has no track record, Jane Street's is private. **The
   live chain is the only evidence that escapes every backtest critique, and
   it compounds daily from zero cost.**

4. **Carry signals money can't buy.** The Far Basin channel ingests
   Palimpsest (palimpsest.info) — censorship intensity as a policy-fear
   confession from the Chinese state, CI-published, keyless. No market data
   vendor carries it. It is quarantined (context only) until it accrues
   enough history to test — which is itself the point: the product's edge is
   that its claims are always exactly as strong as its evidence.

## The honest scoreboard

| Claim | Arbiter |
|---|---|
| The index leads funding events | PROOF: expanding-window recall/precision with Wilson CIs, orthogonal test |
| The forecasts have skill | ML Lab + Tide Tables + Stack: walk-forward Brier/AUROC vs climatology, published verdicts |
| The signal is worth acting on | The Book: net-of-cost P&L vs static mix and all-cash, CI'd, doubled-cost rerun |
| Nobody polished the history | Hash-chained as-published record + PIT blobs + site git history |

Every row has a mandatory negative branch. The day a number is bad, it
prints bad. That discipline — not any single model — is the moat: it
compounds trust the way capital compounds returns, and trust is the only
asset where a free tool can genuinely out-accumulate a $32k terminal.

## Sequencing (positioning: both, sequenced)

Phase 1 (now): prediction quality for the operator — Stack + Book + accruing
chain, private-first. Phase 2 (when the live chain is months deep): the
public case makes itself — a URL where anyone can verify every call this
tool ever published, with the misses left in. That page is the pitch.
