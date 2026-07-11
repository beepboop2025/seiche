---
name: seiche-regime-watch
description: Alerting policy for the Seiche funding-stress board. Use inside scheduled watch jobs, or when asked "should I be worried", "alert me if", or to decide whether a change on the board deserves a notification. Encodes when to speak and, just as important, when to stay silent.
version: 1.0.0
license: AGPL-3.0
metadata:
  hermes:
    tags: [seiche, finance, alerting, monitoring]
    related_skills: [seiche-desk-brief, seiche-ops-watchdog]
---

# Seiche regime watch

An alert that fires on nothing trains the reader to ignore the one that
matters. This skill is the policy for a watch pass: check the board, compare
against the last known state, notify only on a real trigger.

## The watch pass

1. Call `funding_stress_now` and `data_health`.
2. Recall the previous pass's state from memory (regime, index, faults,
   forecast odds). If none is stored, treat this pass as baseline: store
   state, send nothing.
3. Evaluate triggers (below). If none fire, update memory and end silently.
   A silent pass is a successful pass.
4. If a trigger fires, optionally call `funding_stress_forecast` and
   `historical_analogs` to size it, then send one message per pass at most,
   covering all fired triggers together.

## Triggers (any one is sufficient)

- **Regime change** in either direction. CALM→EROSION is worth one calm
  sentence; anything reaching STRAIN or STRESS is the headline case.
- **Index jump**: day-over-day move of 10+ points, even within a regime.
- **Model agreement**: forecast models converging on elevated P(event) at the
  5 or 10 business-day horizon when they previously disagreed.
- **The Tell diverging**: plumbing stress rising while market pricing stays
  relaxed, or the reverse. Quote the Tell reading directly.
- **Novelty**: `historical_analogs` flags today as having no close precedent.
- **Data integrity**: a new fault or stale series in `data_health`, or any
  sign of a gap in the as-published record. Route the details through the
  seiche-ops-watchdog skill; the alert here is one line plus "operator
  notified".

## Alert format

```
SEICHE ALERT — {trigger in six words or fewer}

{regime} {index}/100, was {prev regime} {prev index} at last pass.
{One line per fired trigger, each with the number behind it.}
{One sentence: what usually followed from here, per analogs.}

Board: seiche.info | PROOF-backed, misses shown. Not investment advice.
```

## Anti-noise rules

- At most one alert per trigger type per UTC day; a still-elevated board the
  next morning belongs in the daily brief, not a fresh alert.
- Never alert on a reading you could not ground in a same-session tool call.
- De-escalation is information: when a regime steps back down, one short
  all-clear closes the loop.
- Store the new state in memory at the end of every pass, silent or not.
  The memory entry should carry: date, regime, index, faults, and which
  alerts have already fired today.
