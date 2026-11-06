# Original Federal Reserve releases

`fed_releases.py` qualifies 49 existing catalogue aliases from fixed H.10,
H.15, H.4.1, commercial paper, PRATES and G.17 XML downloads. The reviewed
source dimensions are pinned in `fed_release_contracts.json`. No API key,
subscription, custom DDP package or FRED acquisition is used for these aliases.

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
transaction records. Existing mixed-source engine/publication gates remain
closed until every required dependency is separately qualified.

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
- G.17: monthly total industrial production, seasonally adjusted, 2017=100.
  Publisher month-end reference labels become month-start labels for the existing
  registry contract. Repeated copies across release tables must be identical.
- PRATES: IOER is discontinued history; IORB is a distinct series. Their dates
  are effective/reference dates, not observed publication timestamps.

All historical data are the publisher's current vintage. Acquisition is the
knowledge boundary. Source publication timestamps remain unknown. H.10/H.4.1
publication schedules are expectations and must not become historical receipts.

Bounded tests cover exact definitions, changed units/currencies/frequencies,
missing and nonfinite values, conflicting duplicate datasets, cache isolation,
failed refreshes, export identity and continuing proprietary restrictions.
