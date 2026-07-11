---
name: seiche-proof-audit
description: Answer "can I trust Seiche" with the honest scoreboard. Use when asked about track record, accuracy, reliability, backtest, whether the signal is real, or before anyone acts on a reading. Also the template for periodic credibility reports.
version: 1.0.0
license: AGPL-3.0
metadata:
  hermes:
    tags: [seiche, finance, backtest, credibility, proof]
    related_skills: [seiche-time-machine, seiche-desk-brief]
---

# Seiche PROOF audit

Seiche's stance is that a signal you cannot audit is marketing. The PROOF
scoreboard exists so the trust question has a factual answer. Your job is to
relay it without inflating or softening it.

## The audit

1. Call `proof_backtest`. It returns recall and precision over labelled
   funding events with 95% confidence intervals, an orthogonal robustness
   test, and the full episode list including misses.
2. Call `data_health` to confirm the scoreboard you are quoting sits on a
   current, unfaulted board.
3. Present, in this order:
   - **Recall with its CI**: of the labelled stress events, how many the
     board flagged in advance. Always quote the interval, not just the point;
     the event count is small and the interval is the honest width.
   - **Precision / false-alarm behavior**: how often it cried wolf.
   - **The orthogonal test**: whether the signal survives with the
     mechanically-correlated inputs removed. This is the answer to "is it
     just autocorrelation".
   - **The misses, by name.** Lead with one; unprompted disclosure of a miss
     is what makes the rest believable.
   - **The caveats verbatim** from the tool output.

## Framing rules

- Small-n honesty: with few labelled events, say "N events" out loud. Never
  quote recall without N and the CI.
- Point-in-time: note that readings are as-published, and that the notary
  ledger (hash chain, publicly checkable at the API's notary endpoint, with
  optional Bitcoin anchoring) makes retroactive editing detectable. This is
  the strongest single trust fact; use it.
- Never extrapolate the scoreboard into a promise. The correct claim shape
  is: "over the labelled history it behaved like this; here is where it
  failed."
- If someone asks "so should I trade on it": the answer is that Seiche is an
  early-warning gauge with a published record, decisions and sizing are
  theirs, and it is not investment advice. Every audit ends with that
  sentence.

## Periodic credibility report

When run as a scheduled job (say, weekly), diff the scoreboard against the
last stored one in memory: new episodes labelled, changes in recall or
precision, any new caveat. If nothing changed, the report is one line. Store
the new snapshot in memory either way.
