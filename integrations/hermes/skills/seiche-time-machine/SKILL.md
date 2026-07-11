---
name: seiche-time-machine
description: Point-in-time replay of the Seiche board for historical study. Use when asked "what did Seiche say before X", to walk a past stress episode day by day, to compare today with a named event, or to sanity-check the signal against history. Enforces no-lookahead discipline.
version: 1.0.0
license: AGPL-3.0
metadata:
  hermes:
    tags: [seiche, finance, backtest, history, point-in-time]
    related_skills: [seiche-proof-audit, seiche-desk-brief]
---

# Seiche time machine

`replay_asof` (subscriber tool) reconstructs the whole board as it read on a
past date, using only data knowable then. That makes honest historical
questions answerable: not "does the chart line up in hindsight" but "what did
the board say that morning".

## Core workflows

### Episode walk

To study a named episode (say, the September 2019 repo spike or March 2020):

1. Pick the event date and walk backwards: call `replay_asof` at the event,
   then at roughly -5, -10, -21 business days before it.
2. For each date record: index, regime, which components were loudest, and
   the forward odds if present.
3. Report as a timeline: when the board first left CALM, when it reached
   STRAIN or STRESS, and the lead time versus the event. If it never fired,
   say that in the first line; a miss honestly reported is the product
   working.

### Then versus now

To answer "is today like {past episode}": replay the episode's peak date,
call `funding_stress_now` for today, and compare component by component.
Two boards at the same index can be driven by different plumbing; the
decomposition is the comparison, not the headline number.

### Claim checking

When someone asserts "Seiche would have caught X" or "missed X", do not
argue from the composite chart. Replay the actual dates and quote the
board. For the official hit/miss ledger use `proof_backtest`; the Time
Machine is for the narrative around it, not for recomputing the scoreboard.

## Discipline

- Never mix a replayed date's readings with knowledge of what came after.
  Write each timeline entry as if the later dates do not exist yet, then add
  the outcome as a separate closing line.
- Dates go in as `YYYY-MM-DD`. Weekends and holidays have no board; step to
  the prior business day rather than interpolating.
- Each `replay_asof` call is a metered tool call. An episode walk needs
  4-6 dates, not 40; choose them before calling, state the sampling, and
  widen only if the story demands it.
- When replaying before the data era of a given engine, the tool output may
  omit components. Report the omission; do not fill it in.
