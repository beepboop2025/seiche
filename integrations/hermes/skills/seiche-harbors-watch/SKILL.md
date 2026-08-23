---
name: seiche-harbors-watch
description: Compose the world money-markets watch from the Seiche Harbors panel, native Palimpsest censorship signals, and the rights-safe page archive. Use for "world money markets", "harbors brief", any per-country money-market question, or when the scheduled harbors job fires.
version: 1.0.0
license: AGPL-3.0-or-later
metadata:
  hermes:
    tags: [seiche, finance, money-markets, world, harbors]
    related_skills: [seiche-desk-brief, seiche-regime-watch]
---

# Seiche harbors watch — world money markets

You are writing the note a global money-market analyst wants: each harbor's
water line (overnight anchor, currency, stress), who is easing versus
tightening, and what changed. Every number comes from a live fetch made in
this session. Never quote a reading from memory.

## Sources, in order

1. `data_health` first. A stale or faulted source leads the note; a context
   response is not permission to describe degraded evidence as current.
2. `world_markets_context` with `section="summary"`, then only the relevant
   `money_markets`, `forex`, `capital_markets`, or `china_macro` section. This
   is the backbone: preserve each domain's own as-of clock, evidence status,
   coverage boundary, and canonical citation URL. Do not imply uniformly live
   or exhaustive world coverage.
3. `money_market_context` with `section="summary"` when the question needs the
   granular US countercase. Request only the relevant named detail section;
   use `sources` or `methodology` when the claim needs lineage or a formula.
   This projection is chartless, cache-only, and descriptive rather than a
   tradable or predictive signal.
4. The public `world_markets_context` China section is metadata-only unless a
   restricted owner-attested revision is served. `knowledge_time` dates the
   owner's capture, not an observation. Owner attestation is not an NBS
   digital signature; never infer withheld values, construct a local rate, or
   advance a market clock from response time. Federal Reserve H.10 CNY FX may
   remain available as separately sourced context.
5. `https://palimpsest.info/readings/ddti-latest.json` for Palimpsest's native
   censorship targets and deletion-threat context. These are information-
   controls signals, not money-market benchmarks; keep them in a clearly
   labelled context sentence and never turn them into a China rate.
6. The page archive at `~/mm-archive/data/<YYYY-MM-DD>/` (one HTML snapshot
   per official page per day, manifest.jsonl alongside). Use it when asked
   what an official page said or when it changed; cite the snapshot date.
   If today's directory is missing or the manifest shows failures, report
   that as an ops fact. Do not read or quote archived ChinaMoney/CFETS values.

## Output shape (chat-sized)

```
HARBORS {date} — cycle: {n} easing / {n} holding / {n} tightening (US ref EFFR {x}%)

{one line per harbor, hottest first:}
{HARBOR} — {rate}% ({cadence-honest asof}) · stress {s} · {REGIME} · {fx line}

{One or two sentences: what moved since the last watch, which harbor to
watch next, and any data caveat (accruing history, stale feed, failed
snapshot).}
```

## Rules

- A missing component is stated, not papered over: "China local rate and
  regime unavailable; the served China view is metadata-only, while separately
  sourced H.10 CNY FX remains available" is the correct boundary when that is
  what the response says. Absence never means calm.
- No cross-country stress ranking language like "India is more stressed than
  Japan" — each stress is against its own history. "India is unusual for
  India" is the honest form.
- Never fetch or publish `china-econ-*`, ChinaMoney, SHIBOR, FR/FDR/DR007, or
  central-parity values. Fail closed even if a mirror, cache, or archive has a
  fresher number. The only current China market input in this watch is the
  Federal Reserve H.10 CNY FX series exposed by the Seiche board.
