# Original Federal Reserve releases

`fed_releases.py` qualifies 49 existing catalogue aliases from fixed H.10,
H.15, H.4.1, commercial paper, PRATES and G.17 XML downloads. The reviewed
source dimensions are pinned in `fed_release_contracts.json`. No API key,
subscription, custom DDP package or FRED acquisition is used for these aliases.
Only the release-page XML zips are read: the Board announced it will remove
the Data Download Program's Build Your Package option the week of 9 November
2026, ahead of retiring the DDP. All six zips parsed against these contracts
on 2026-11-09.

Run against the intended isolated or production runtime data directory:

```sh
SEICHE_RUNTIME_DATA_DIR=/absolute/runtime/data \
  python -m seiche.sources.fed_releases --receipt /absolute/qualification.json
```

The command collects only these sources. It does not deploy, publish, modify
timers, submit marketplace forms or contact anyone. It returns failure when a
source failed to refresh or a contracted alias is missing. A retained previous
capture remains visibly dated and does not turn a failed refresh into success.

Each release is one atomic, self-contained SQLite blob in an
`original-publisher:fed:*:v1` namespace. It contains the exact source URL,
retrieval timestamp, response SHA-256, normalized observations, their SHA-256,
and the pinned definition/rights contract. Old `observations` and `fetches`
rows are neither imported nor relabelled. Catalogue and CSV/JSON exports read
only qualified captures for migrated aliases. A cold or invalid capture stays
restricted; there is no FRED fallback. Normal assembler refreshes use the same
collector through `fred.fetch_many` to preserve existing engine group names.

The [Board's reuse policy](https://www.federalreserve.gov/disclaimer.htm)
permits reuse of its public-domain material with attribution. The scope here
is these Board-published statistics. It excludes logos and underlying private
transaction records. A mixed-source engine or publication is public only when
every input it reads is qualified, under the per-engine projection in
`seiche/public_rights.py` (policy seiche.public-source-rights.2026-11-09).

Definition details that must remain explicit:

- H.10: currency quote direction is pinned; the three current dollar indexes
  use January 2006 average=100. Their XML contains a legacy unit label. The
  [publisher's technical Q&A](https://www.federalreserve.gov/releases/h10/h10_technical_qa.htm)
  establishes the current definition; values are not rescaled.
- H.4.1: WALCL uses the consolidated `TOTC` series. Reserve balances,
  Treasury General Account and currency circulation use weekly averages;
  custody, primary credit, swaps and foreign reverse repos use Wednesday levels.
- Commercial paper: AA 90-day financial/nonfinancial published rates only.
  `NA`, `ND` and `NC` are missing per `frb_common.xsd`, never zero or filled.
  The Board derives the release from DTCC data. The owner accepted that origin
  on 2026-11-08 (`seiche.source-use.2026-11-08.fed-cp` in
  `seiche/source_decisions.json`); without that record a CP capture decodes
  with redistribution status `owner_decision_required`. Exports name the DTCC
  derivation and never carry the underlying trade records.
- G.17: monthly total industrial production, seasonally adjusted, 2017=100.
  Publisher month-end reference labels become month-start labels for the existing
  registry contract. Repeated copies across release tables must be identical.
- PRATES: IOER is discontinued history; IORB is a distinct series. Their dates
  are effective/reference dates, not observed publication timestamps. A rate
  the file lists before its effective date is excluded and counted
  (`excluded_future_effective_observations`) until it takes effect, the same
  rule as the atlas PRATES adapter, instead of invalidating the capture.

All historical data are the publisher's current vintage. Acquisition is the
knowledge boundary. Source publication timestamps remain unknown. H.10/H.4.1
publication schedules are expectations and must not become historical receipts.

Bounded tests cover exact definitions, changed units/currencies/frequencies,
missing and nonfinite values, conflicting duplicate datasets, cache isolation,
failed refreshes, export identity and continuing proprietary restrictions.
