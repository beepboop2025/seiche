# ECB daily foreign-exchange reference history

`seiche.sources.ecb_fx.fetch(client, faults=None, mode="auto", force=False)`
returns a mapping of `ECBFX_{currency}` to the existing `Series` envelope and
persists it through `store.save_series_batch`. The collector belongs in the scheduled
legacy source sweep. Public requests should read stored series only.

## Observation contract

| Field | Value |
| --- | --- |
| Source | `ecb_fx` |
| Mnemonic | `ECBFX_USD`, `ECBFX_CNY`, etc. |
| Remote ID | `EXR/D.USD.EUR.SP00.A`, with the quote currency substituted |
| Unit | `USD/EUR`, `CNY/EUR`: quote-currency units per one EUR |
| Frequency | `D`, daily reference observations |
| Event date | Actual parent XML `Cube time` date |
| Capture clock | Current UTC response acquisition time, carried as `fetched_at` |
| Publication clock | Unknown in this XML; never substituted with capture time |

The registry exports 29 current currencies as `CURRENCIES`, and the collector
returns only those registered currencies. A historical archive can contain
retired/suspended currencies as well. Those remain stored with their actual last
observation dates; absent dates/currencies are not filled. Crosses
must join observations from the same provider and date. CNY per USD is
`(CNY/EUR) / (USD/EUR)` and must be labelled derived; it is neither PBOC central
parity nor offshore CNH or a tradable quote.

## Collection and persistence

The collector fetches all currencies in one official XML file:

- Full history: `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml`
- Rolling 90 days: `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml`
- Latest date: `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`

`auto` downloads full history on bootstrap and every 30 days, then uses the
rolling 90-day file between full refreshes. The normal cache TTL is six hours.
Explicit `history`, `history90d`, and `daily` modes support bounded operational
collection; `force=True` skips the TTL. Rolling windows merge into existing
history through the series store; observation vintages retain acquisition time.
Historical backfills become known now, not at their historical observation date.

Downloaded/decompressed content is limited to 16 MiB and redirects are rejected.
A 90-second deadline covers the entire download, in addition to the 60-second
HTTP operation timeout, so a slow stream cannot indefinitely occupy the worker.
Only UTF-8 ECB envelopes are accepted. DOCTYPE, ENTITY and null bytes are rejected
before XML parsing. The entire document is checked for expected structure,
publisher, duplicate dates/currencies, valid dates and finite positive rates
before any series or raw capture is written.

The existing `FileRawCaptureSink` archives exact source bytes under
`DATA_DIR/raw/market=GLOBAL-FX/source=ecb_fx/date=YYYY-MM-DD/{sha256}.xml`.
Conflicting bytes at an existing hash path cause failure. Capture manifests
include source URL, SHA-256, bytes, capture time, date coverage, observation count,
series list and current currencies. They are stored at
`ecb_fx:capture:{sha256}:{mode}:{fetched_at}`, with completed pointers at `ecb_fx:latest`
and `ecb_fx:full-history`. `store.save_series_batch` commits all currency
observations, their vintages, and the capture manifests in one SQLite
transaction. Failure rolls back the entire generation; an unreferenced immutable
raw archive may remain as evidence of the attempted acquisition.

Cache admission requires every registered currency, the exact source, remote
identity, EUR-base units and daily cadence. Every current series must share the
manifest's timezone-aware capture clock and latest observation date. The source
URL, immutable capture-manifest binding and chronological bounds are validated.
A partial or mixed-generation cache cannot qualify for the six-hour TTL shortcut.

When a refresh fails, `faults` receives an explicit source failure and existing
data retain their original clocks. Without a fault sink, errors are raised even
if a cache exists, preventing silent stale success.

## Scheduler integration

Minimal integration, owned by the parent change:

1. Import `ecb_fx` in `assemble.py` and add
   `guard("ecb_fx", ecb_fx.fetch(client, faults))` to `_gather_sources()`.
2. Add immutable `ECB_FX_SERIES` specs to `config.py` and `ALL_SERIES` using the
   observation contract above. Keep current 29-currency registration distinct
   from the historical archive's wider retired-currency coverage.
3. If replay/provenance assembly consumes the group, include `ecb_fx` in its
   relevant truncation and provenance groups. Request-facing workbench reads
   should use the bounded persistent series reader.
4. No separate `ingest_runtime.py` hook is required:
   `collect_legacy_once()` already invokes `assemble._gather_sources()` and its
   worker loops persistently. Activate this existing worker on the compute host.

## Source terms and meaning

[ECB methodology](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
states that currencies are quoted against the euro, and rates are informational
reference rates usually updated around 16:00 CET on applicable working days.
They are unsuitable as an execution-price feed.

[ECB terms](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html)
permit accurate reuse with source attribution. Identify calculations or other
modifications explicitly. For paid access, tell purchasers the source data are
available free from the ECB before payment and whenever that information is
accessed. Preserve these conditions in display/export metadata.
