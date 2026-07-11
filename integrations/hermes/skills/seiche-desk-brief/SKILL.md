---
name: seiche-desk-brief
description: Compose a funding-stress desk brief from the live Seiche board. Use when asked for a morning note, an evening check, "what's the funding picture", or when a scheduled brief job fires. Produces a short, grounded note sized for a chat message.
version: 1.0.0
license: AGPL-3.0
metadata:
  hermes:
    tags: [seiche, finance, funding, brief]
    related_skills: [seiche-regime-watch, seiche-proof-audit]
---

# Seiche desk brief

You are writing the desk note a funding analyst would want first thing: the
regime, what moved, what the odds say, and whether the data underneath is
sound. Every number comes from a Seiche MCP tool call made in this session.
Never quote a reading from memory.

## Tool call order

1. `data_health` first, always. If any input series is stale or faulted, the
   brief must open with that fact. A reading on bad data is worse than no
   reading.
2. `funding_stress_now` for the index (0-100), the regime word
   (CALM / EROSION / STRAIN / STRESS), the component decomposition, and the
   Tell.
3. `funding_stress_forecast` (subscriber) for P(event) at 5/10/21 business
   days across the model fleet. Agreement between independent models is the
   signal; one model alone is noise.
4. `historical_analogs` for the nearest past days and how often they led to a
   stress event. Note the novelty flag: if today has no close precedent, say
   so instead of forcing an analogy.
5. `desk_brief` (subscriber) for the full prose note. When available, use it
   as the backbone and compress; do not re-derive what it already states.

If a subscriber tool returns an authorization or quota error, write the brief
from the public tools and say plainly which sections are missing and why.

## Output shape (chat-sized)

```
SEICHE {date} — {REGIME} {index}/100 ({+/-delta} d/d)

Driving it: {top 2-3 components, one line each}
Forward odds: {5bd}% / {10bd}% / {21bd}% — models {agree|split}
History says: {analog outcome rate, or "no close precedent (novel)"}
Data: {all fresh | name the stale/faulted series}

{One or two sentences of judgment: what changed since the last brief and
what would change the read.}

Track record: PROOF scoreboard at seiche.info (recall/precision with CIs,
misses included). Not investment advice.
```

Keep it under roughly 20 lines. Bold the regime word if the platform renders
markdown. No hedging filler; if the board is boring, one line saying so is a
complete brief.

## Timing context

The reference deployment pulls new data at 12:15 and 20:45 UTC. A brief
composed just before a pull describes yesterday's water; say which board
timestamp you are reading (it is in the tool output).

## Discipline

- Cite the index and regime exactly as returned; never round the regime up
  or down by feel.
- If `data_health` and the index disagree in spirit (fresh faults but a calm
  read), lead with the fault.
- Close with the PROOF citation and "Not investment advice." every time.
  These two lines are non-negotiable house style.
