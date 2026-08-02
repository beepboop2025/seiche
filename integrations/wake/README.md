# wake → seiche integration

[`~/dev/wake`](../../../wake) is the lab's institutional-positioning
nowcast engine (hedge funds / pensions / sovereigns from public prints
only). This directory wires its **seiche pack** into the board without
touching engines until the wiring is deliberately reviewed.

## What seiche gains

Seiche already collects TFF UST positioning (`sources/cftc.py`). Wake
adds the *method layer* on top, plus one series seiche lacked:

* **basis_nowcast** — Barth & Kahn (JME 2025) basis-trade size proxy:
  leveraged-fund net short UST notional, z vs 3y, 4-week delta, and a
  `fragile` flag when size is stretched while SOFR−IORB is elevated or
  rising (Dallas Fed 2026 configuration for forced deleveraging).
* **sovereign_custody** — H.4.1 foreign-official Treasury custody
  (FRED `WMTSECL1`, weekly W+1): the fastest free print of sovereigns
  adding/shedding USTs. Not currently in `sources/fred.py`.
* **fusion_index** — mixed-frequency Kalman nowcast fusing the above
  with the repo spread into one daily latent positioning-stress index
  with 68% bands; strict release-date gating (a Tuesday CoT print
  enters only after its Friday release), matching PROOF discipline.
* **stress_endogeneity** — Hawkes branching ratio on repo-spike
  events: the fraction of stress events caused by earlier stress
  events, with an LR test vs Poisson. A natural companion cell to the
  event-odds ensemble.

## Wiring (two steps, both her call)

1. Generate the pack (cron on the box or Mac):
   `python3 -m wake.cli live --out /path/to/packs`
2. Read it here: `wake_source.load(path_or_env)` returns the validated
   payload; `readings()` flattens the board-relevant numbers. Engine
   wiring (a board cell / brief line) is deliberately NOT done in this
   branch — sources first, judgment wiring after review.

Pack contract: envelope with `schema_version`, `method_versions`,
`provenance[]` (reference/release/retrieved timestamps per source) —
see `wake/packs.py`. Everything US-federal public domain.
