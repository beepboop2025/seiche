---
name: seiche-harbors-watch
description: Compose the world money-markets watch from the Seiche Harbors panel, native Palimpsest censorship signals, and the rights-safe page archive. Use for "world money markets", "harbors brief", any per-country money-market question, or when the scheduled harbors job fires.
version: 1.0.0
license: AGPL-3.0
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

1. `https://api.seiche.info/api/overview` → `engines.harbors`. This is the
   backbone: per-harbor `rate` (level, asof, cadence), `fx` (level, 60d move,
   vol), `stress` (0-100, percentile of the harbor's OWN history — never
   compare stress levels across countries as if calibrated), `regime`
   (EASING / HOLDING / TIGHTENING), and `cycle` counts with the US EFFR
   reference. Honor the cadence labels: India and Korea anchor rates are OECD
   monthly mirrors ~2 months lagged BY DESIGN; Japan's TONA is daily. China's
   row is FX-only from Federal Reserve H.10 CNY. Its local rate and regime are
   unavailable, not calm, until redistribution permission is established.
2. `https://palimpsest.info/readings/ddti-latest.json` for Palimpsest's native
   censorship targets and deletion-threat context. These are information-
   controls signals, not money-market benchmarks; keep them in a clearly
   labelled context sentence and never turn them into a China rate.
3. The page archive at `~/mm-archive/data/<YYYY-MM-DD>/` (one HTML snapshot
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

- A missing component is stated, not papered over: "China local rate and regime:
  unavailable pending redistribution permission; H.10 CNY FX remains live" is
  the correct boundary. Absence never means calm.
- No cross-country stress ranking language like "India is more stressed than
  Japan" — each stress is against its own history. "India is unusual for
  India" is the honest form.
- Never fetch or publish `china-econ-*`, ChinaMoney, SHIBOR, FR/FDR/DR007, or
  central-parity values. Fail closed even if a mirror, cache, or archive has a
  fresher number. The only current China market input in this watch is the
  Federal Reserve H.10 CNY FX series exposed by the Seiche board.
