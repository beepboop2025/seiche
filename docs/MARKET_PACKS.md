# Seiche market-pack architecture

Status: production data plane deployed behind v2 contracts. The public product
must not claim broad validated support while packs remain `REFERENCE` or
`VALIDATING`; operational collection readiness is not model validation.

## Boundary

```text
official / licensed / tenant evidence
                 │
      independently scheduled adapter
                 │
      monetary-area pack semantics
       roles · clocks · calendars
                 │
      canonical bitemporal observations
                 │
         universal role engines
          │                 │
  sealed local gauge   sealed global tide
```

The universal kernel imports only canonical observation roles. Source mnemonics,
jurisdiction names, policy institutions, local calendar rules, and licensing
terms live outside it. Onboarding a market therefore registers another pack and
its adapters; it does not edit a universal calculation.

## Time model

Every canonical row carries three distinct clocks:

- `event_time`: when the measured market event occurred or became effective;
- `source_publication_time`: when the source published that particular row;
- `knowledge_time`: when the row became knowable to this Seiche record.

A source-native calendar date is a market business-date/session label, not a
local-midnight instant. Canonical adapters encode such labels as `00:00:00Z` so
the same labelled session has the same key in Sydney, Mumbai, New York, and
every other pack. A source-native datetime is different: it remains an actual
instant, is interpreted in the pack timezone only when naive, and is converted
to UTC without session coercion. Global Tide consumes only the canonical
UTC-midnight daily keys. It intersects common level dates before taking changes,
so a holiday in one market cannot compare a one-session move with another
market's multi-session move; it never forward-fills or weekday-guesses a
cross-market session.

Publication and knowledge clocks remain separate. An upstream timestamp is
retained when supplied. Otherwise the pack's declared business-day lag and
local publication time are used; when no time is declared, the conservative
fallback is `23:59:59` in the publication-day timezone. A row whose publication
clock is later than capture is withheld. Every newly seen row becomes knowable
at capture time, never retroactively at its reported or inferred publication
time. An unchanged row preserves its first knowledge time, while a revision
becomes knowable at the capture that discovered it.

Rows are append-only across revisions. An as-of query first filters by knowledge
cutoff, then selects the latest knowable revision for each instrument/event pair.
Legacy `Series.fetched_at` remains for v1 compatibility but is not eligible for
canonical historical prediction.

The one-time historical import is deliberately conservative. A source's current
historical file retains each row's declared source-publication clock, but the row
is marked `PROVISIONAL` and becomes knowable at the time Seiche captured that
file. A current vintage is never retroactively treated as the vintage that was
available in the past. These rows can calibrate today's within-market history;
only subsequent real-time captures and revisions can build a forward record.

## Pack lifecycle

- `REFERENCE`: typed semantics exist, but collection/calendar/calibration work is
  incomplete.
- `VALIDATING`: a live migration pack is accruing evidence and parity records.
- `SUPPORTED`: all required validation checks have an evidence-backed `PASS`.

`MarketPack` rejects `SUPPORTED` at construction time unless all eleven checks
are present: schema/units, calendar/timezone, truncation, reporting lag,
revision leakage, label shuffle, source-failure injection, local holdout,
leave-one-market-out, forward record, and US parity.

## Collection and serving

`CollectorSupervisor` schedules `(market_id, adapter_id)` independently. Retries,
backoff, cadence, and circuit state are source-local. A failed task returns a
scoped fault and cannot cancel successful tasks for another market.

Raw responses are content-addressed before parsing. Normalized Parquet parts are
partitioned as:

```text
market=<market_id>/source=<adapter_id>/date=<event-date>/part-<hash>.parquet
```

Production mounts raw and normalized roots at `/var/lib/seiche`. The idempotent
`ops/deploy/install-market-platform.sh` provisioner installs PostgreSQL, creates
the peer-authenticated `seiche` role/database, installs the market worker and
backfill units, and injects the repository environment into `seiche-api`.
Normalized evidence is immutable Parquet; canonical observation metadata,
collector outcomes, snapshots, and forward records live in PostgreSQL.

The worker also emits the private, training-only
[`us-usd-funding-core-v1`](US_USD_FUNDING_CORE_V1.md) input pack after completed
US collection cycles. It is a pinned three-instrument research export with an
exact no-imputation event intersection, not another API or a fitted model.

Local development retains the same repository protocol over SQLite. Setting
`SEICHE_DATABASE_URL` selects PostgreSQL without changing an adapter, engine, or
API contract. PostgreSQL 11 is the minimum supported server because additive
constant-default migrations rely on its metadata-only behavior. The dedicated
`market-platform-ci` workflow executes the complete repository contract against
PostgreSQL 17.

The production commands are also available directly:

```text
seiche market-collect [--market EA-EUR]
seiche market-backfill [--market EA-EUR]
seiche market-worker --poll-seconds 30
seiche market-validate [--market EA-EUR]
```

Backfill completion is marked per adapter. A successful source is not fetched
again merely because another adapter failed; only incomplete sources retry.
Collector outcomes and local snapshots publish in completion order, so a slow
or retrying foreign adapter cannot delay a finished market. The Global Tide is
sealed at the end of the due cycle because it is inherently cross-market.

Collector outcomes have four explicit states. `SUCCESS` means observations
were durably written. `UNAVAILABLE` means a local access policy deliberately
withheld the request, so it performs no retry and does not increment or open a
source circuit. `FAILED` means a real acquisition, parsing, or persistence
fault. `CIRCUIT_OPEN` means repeated real failures are cooling down. Every
non-`SUCCESS` state remains a product fault and prevents the affected inputs
from being treated as fresh. An all-`UNAVAILABLE` backfill exits successfully
as an operational abstention, but writes no completion marker and therefore
does not claim that source history was imported.

## Artifact-backed validation

`market-validate` now executes the gates that can be tested from canonical
point-in-time data and writes one immutable, content-addressed JSON artifact per
market/check. Each artifact records its event and knowledge cutoffs, input and
pack fingerprints, runner policy version, metrics, reasons, and evidence
references. An exact retry is idempotent; changed bytes cannot overwrite an
existing artifact. Production provisions these records under
`/var/lib/seiche/validation` through `SEICHE_VALIDATION_DIR`; local development
defaults to `backend/data/market_validation`. The production provisioner also
enables an independent daily `seiche-market-validation.timer`. Its oneshot unit
treats exit `2` (`PENDING`) as a successful evidence-accrual run, while exit `1`
(`FAIL`) remains an operational failure; it has no dependency on the API or
collector worker.

Examples:

```text
# Run every gate for every registered market.
seiche market-validate

# Run selected point-in-time gates for India. The timestamp must carry an offset.
seiche market-validate --market IN-INR \
  --check schema_and_units --check calendar_and_timezone \
  --as-of 2026-08-09T12:00:00+05:30

# Record the forward-maturity policy used by the integrity assessment.
seiche market-validate --market US-USD --check forward_paper_record \
  --minimum-forward-records 250 --minimum-forward-span-days 365

# Verify the newest artifact for all eleven gates without rerunning or promoting.
seiche market-validate --market US-USD --promotion-report
```

The command emits one batch JSON object. Exit `0` means every selected gate for
every selected market passed, `1` means at least one hard failure, and `2` means
there were no hard failures but evidence remains pending. In a multi-market
run, a hard failure outranks a pending result. An exception in one market is
reported as that market's failure while sibling markets continue.

The executable evaluators cover declared schema/unit coherence, dated calendar
and timezone bounds, genuine future-suffix truncation perturbation, extra
reporting-lag perturbation, real revision/vintage pairs, missing-required-source
injection, and forward-record chain integrity/maturity. They fail closed or
return `PENDING` when the necessary real observations are absent. Label shuffle,
local temporal holdout, leave-one-market-out review, forward outcome review,
and US v1/v2 parity remain explicitly pending until their dated corpora and
review records exist. The promotion report also rejects stale artifacts when a
pack contract or runner policy changes. It is a verifier only and never edits a
registered pack.

The v2 API never invokes collection. It reads only canonical observations and
sealed snapshots. The US cycle continues to materialize `US-USD` through its
pack-local compatibility bridge, preserving v1 parity. Other packs invoke the
universal semantic engines through versioned `*-local-forward-vN` calibrations.
Required component absence produces `UNAVAILABLE`; stale required evidence may
publish only as `DEGRADED`.

## Product contracts

- Local Seiche Gauge: one monetary area, local calibration, explicit missing
  capabilities and stale/faulted inputs.
- Global Seiche Tide: cross-basin transmission only. It never averages local
  gauge values. It is materialized even when unavailable, with a null reading
  and a precise missing-capability reason. It becomes numeric only with at
  least two aligned `FX_SWAP_BASIS` histories.

Only `allowed` and `derived_only` rows may enter public calculations.
`metadata_only` rows may describe availability in the v2 series contract, but
their values are redacted and never feed a local gauge or Global Tide.
`prohibited` rows and source metadata are omitted from public projections.

## Current adapter coverage

As of 2026-08-14, live sandbox backfill tests complete for 19 of 21 official
adapter schedules across USD, EUR, GBP, JPY, CNY, HKD, INR, AUD, and SGD. Both
RBNZ schedules are unavailable because production has no recorded prior written
permission for automated RBNZ website access. The adapters fail closed before a
network request unless a root-controlled approval hash and bounded re-review
date are installed. A browser-shaped request was useful diagnosis but is not a
lawful production fallback. The current B2 workbook parser is ready and
validates exact series IDs without downgrading a changed modern schema to fuzzy
headings.
See the [RBNZ access incident runbook](RBNZ_ACCESS_INCIDENT_2026-08-14.md).
Seiche still does not relabel RBNZ's mixed secured/unsecured overnight cash rate
to manufacture a result.

That 19-of-21 statement is a dated validation receipt, not the current catalog
count. The 2026-08-21 factory declares 23 deployable official schedules across
11 packs after adding Korea's BOK policy and overnight-call adapters. Those two
adapters require an individually issued `SEICHE_BOK_ECOS_API_KEY` and fail
closed when it is absent; KOFR remains metadata-only until its redistribution
and operational contract can be promoted safely.

The CFETS adapter is likewise catalogued but unavailable by default. It makes
no request unless root-controlled approval and licence-evidence artifacts bind
the exact schema/FDR007/SHIBOR endpoints, upstream products, canonical outputs,
selected-field retention, internal-research-only use, prohibited publication,
and a review deadline no more than 366 days ahead. FDR007 is the fixing
calculated from underlying DR007 transactions; the adapter does not relabel it
as DR007. Approval is revalidated before every data request and does not change
the pack's `metadata_only` redistribution status. See the
[CFETS access boundary](CFETS_ACCESS_BOUNDARY.md).

Licensed and tenant adapters remain declarations until an entitled deployment
supplies credentials/data. Their absence is a capability state, never a zero.

### US direct facility results (2026-11-02)

The `nyfed_facilities` adapter reads two NY Fed Markets API result feeds under
the same adapter record, rights, clock and validation: repo operations
(`/api/rp/repo/all/results/last/N.json`) give `US.NYFED.SRF_TAKEUP`, and
reverse repo operations (`/api/rp/reverserepo/all/results/last/N.json`) give
`US.NYFED.ON_RRP_TAKEUP` (mnemonic `ON_RRP_ACCEPTED`, USD billions at source,
local-currency millions canonical). ON RRP is the sum of accepted overnight
reverse repos on each operation date, including the occasional small value
exercise, which is the published daily total of overnight reverse repurchase
agreements; term operations and other operation types are excluded. A date
with any unreported accepted amount is withheld, never zero-filled. ON RRP is
declared after SRF, so pack order keeps SRF as the kernel's facility take-up.
A failed fetch of either feed faults the whole adapter run rather than leaving
one series silently stale. The FRED copy (`RRPONTSYD`) remains withheld.

### US direct reserve balances and total assets (2026-11-02)

`seiche.sources.fed_h41` reads reserve balances and total assets from the
Board's own H.4.1 release package
(`https://www.federalreserve.gov/releases/h41/data/FRB_H41_xml.zip`), not FRED
and not the Data Download Program tool the Board is retiring. Series
`RESH4R_XAW_N.WW` ("Reserve balances with Federal Reserve Banks: week average",
USD millions, Wednesday dates) is the number FRED republishes as `WRESBAL`;
series `RESPPMA_N.WW` ("Total assets (Less eliminations from consolidation):
Wednesday level", USD millions) is the number FRED republishes as `WALCL`.
The shared Board package reader (`seiche.sources.frb_release`) streams the
package once and refuses XML declarations; the parser pins each series'
declared frequency, unit, multiplier and currency, requires both series,
rejects duplicate, non-Wednesday or invalid rows, and skips `NA` rows rather
than zero-filling.

Rights: the Board's notice at `https://www.federalreserve.gov/disclaimer.htm`
("Copyright/trademark", captured 2026-11-02) reads: "Unless otherwise
indicated, information on Board's website is in the public domain and may be
copied and distributed without permission. Please cite to the Board as the
source of the information." The source registry records this as
`frb_h41_release` (`explicit_public_domain_with_exclusions`), and the US pack
declares the official-open `fed_h41` adapter with redistribution allowed:
weekly cadence, polled daily, released Thursday 16:30 New York with one
business day of lag on the US Fedwire calendar. The instrument is
`US.FED.RESERVE_BALANCES_DIRECT` (mnemonic `RESERVE_BALANCES_DIRECT`, USD
millions), declared before the FRED copy so pack order prefers it, and
`US.FED.TOTAL_ASSETS_DIRECT` (mnemonic `TOTAL_ASSETS_DIRECT`, USD millions,
role `CENTRAL_BANK_TOTAL_ASSETS`, which no capability reads). The adapter keeps
only rows for the instruments the pack declares on it.

Every served value carries the citation `Source: Board of Governors of the
Federal Reserve System, H.4.1` with `terms_url`: on the atlas metric, on the
series page's instrument reference and in the atlas `legal_notices`. The
adapter is still registered only while the pack declares that allowed record;
any other declaration makes no request and serves nothing. The FRED copies
(`US.FED.RESERVE_BALANCES` on `fred_weekly`, and the legacy `WALCL` series)
stay metadata-only and redacted. Routine collection keeps the last 45 days; a
deeper history needs a one-off `seiche market-backfill --market US-USD` after
release.

### US direct Treasury par yields (2026-11-02)

`seiche.sources.treasury_par_yields` reads the Daily Treasury Par Yield Curve
Rates from Treasury's documented XML feed
(`https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve`),
by month for routine collection (`field_tdr_date_value_month=YYYYMM`) and by
year for a backfill (`field_tdr_date_value=YYYY`). The US pack declares eight
tenors on adapter `treasury_par_yields`: `US.TREASURY.PAR_YIELD_1M`, `_3M`,
`_6M`, `_1Y`, `_2Y`, `_5Y`, `_10Y` and `_30Y` (feed fields `BC_1MONTH` to
`BC_30YEAR`), each a `SOVEREIGN_YIELD` in basis points with the source's own
bond-equivalent convention. They are not the DTB3 discount-basis bill rate on
`US.TREASURY.TBILL_3M`, so no existing role binding moves. The parser checks
the feed title, the declared OData types, that every date lies in the
requested month or year on a weekday, and a bound on values; it refuses XML
declarations, skips absent or null tenors rather than zero-filling, and keeps
the request URL out of the row evidence so a backfill re-reading a published
row is not a revision.

Rights: Treasury computes the curve itself ("The Treasury's official yield
curve is a par yield curve derived using a monotone convex method", Treasury
Yield Curve Methodology page), so it is a work of the United States
Government, and 17 U.S.C. 105(a) reads: "Copyright protection under this
title is not available for any work of the United States Government".
Treasury's Site Policies and Notices page
(`https://home.treasury.gov/subfooter/site-policies-and-notices`) and the
Privacy and Legal Notices page it links, checked 2026-11-02, carry no copyright
statement and no reuse restriction. The registry records this as
`treasury_par_yield_curve` (`explicit_public_domain_with_exclusions`, evidence
the statute), and every served value carries `Source: U.S. Department of the
Treasury, Daily Treasury Par Yield Curve Rates` with that terms page, on the
atlas metric, the series page and the atlas legal notices. Treasury seals and
names and the NY Fed indicative quotations behind the curve are excluded.

Clock: Treasury says the rates "are usually available at Treasury's interest
rate website by 6:00 PM Eastern Time each trading day", so the adapter is
daily, polled hourly, published 18:00 New York with no lag on the Fedwire
calendar. A row seen before 18:00 on its own day waits for the next run. The
bond market can close on a day Fedwire is open (Good Friday), which can age the
rows by one opportunity until the next posting. The FRED copies (the legacy
`DGS3M`, `DGS2`, `DGS10` and `DGS30` series and DTB3) stay metadata-only. A
deeper history needs the same one-off `seiche market-backfill --market US-USD`.

### US commercial paper: implemented, off pending rights (2026-11-02)

`seiche.sources.fed_cp` parses the 90-day AA financial and nonfinancial rates
(`RIFSPPFAAD90_N.B`, `RIFSPPNAAD90_N.B`) from the Board's own CP release
package (`https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip`) and
skips `ND` rows, which the release prints as "n.a." when trades were
insufficient. It is off: the release page is headed "Derived from data supplied
by The Depository Trust & Clearing Corporation" and the About page states that
the release "is derived from data supplied by The Depository Trust & Clearing
Corporation (DTCC)", which may take these rates outside the Board's "Unless
otherwise indicated" public-domain notice; FRED marks its H.15 copies "Data in
this graph are copyrighted". The registry records `frb_commercial_paper` as
`review_required` and `policy_blocked`. The US pack declares no `fed_cp`
adapter, so no request is made and nothing is stored or served, and the FRED
copies (`US.CP.NONFINANCIAL_3M`, legacy `CP_FIN_3M` and `CP_NONFIN_3M`) stay
metadata-only. Enabling needs an owner rights decision recorded in the
registry and an allowed pack declaration; no code change.

### US direct Board release packages (0.17.0 candidate, Stage 2a)

`seiche.sources.frb_release_package` is one declarative adapter for the
Board's own release packages at
`https://www.federalreserve.gov/releases/<rel>/data/FRB_<REL>_xml.zip`, the
files each release page links as "XML", not FRED and not the Data Download
Program tool the Board is retiring. It generalizes the 0.16.12 `fed_h41`
parser; `fed_h41` and `fed_prates` are now thin modules over it, and a test
pins that every row they stored before keeps its exact evidence bytes and
revision ID. Each package pins its ZIP member, its SDMX data set and, per
series, the exact publisher declarations (frequency, unit, multiplier and the
dimensions that identify the instrument, such as H.4.1 `SERIESTYPE`, H.15
`INSTRUMENT` and `MATURITY`, H.10 `FX`, G.17 `SA`). Every pinned series must
appear exactly once in the pinned data set, dates must be strictly increasing
on the package's calendar rule (Wednesdays, weekdays, month ends or any day),
and every available value must be a plain decimal inside the package bounds.
`NA` and `ND` rows stay absent. Any change fails the whole run closed. The
archive, the member's declared and streamed size, the member count and the
rows per series are bounded, and the fetch takes no redirects.

| Adapter | Package | Series served | Role | Clock |
|---|---|---|---|---|
| `fed_prates` | PRATES | `RESBM_N.D` IORB (existing) and `RESBME_N.D` IOER, 2008-10-09 to 2021-07-28 (`US.FED.IOER_DIRECT`) | `POLICY_TARGET`; IOER `RETIRED_ADMINISTERED_RATE` | unknown, as before |
| `fed_h41` | H.4.1 | reserves and total assets (existing) plus the TGA and currency week averages and primary credit, liquidity swaps, foreign official reverse repos and repos, and custody holdings of Treasuries, Wednesday levels | `CENTRAL_BANK_BALANCE_SHEET_ITEM` for the seven added lines | Thursday 16:30, as before |
| `fed_h15` | H.15 | constant maturities 3M, 2Y, 10Y, 30Y; bills 4W and 3M, discount basis | `SOVEREIGN_YIELD`; bills `TBILL_DISCOUNT_RATE` | next business day 16:15, polled every 3 hours |
| `fed_h10` | H.10 | 22 bilateral noon buying rates in the release's own direction and the Broad, AFE and EME nominal dollar indexes | `EXCHANGE_RATE`, `CURRENCY_INDEX` | weekly Monday 16:15 posting, polled every 6 hours |
| `fed_g17` | G.17 | `IP.B50001.S`, total industrial production, seasonally adjusted, 2017=100 | `INDUSTRIAL_PRODUCTION` | unknown (mid-month), polled daily, 400-day routine window |

None of the new roles is read by a capability, an event, the universal kernel,
the v1 composite, the Tell, ML or any engine, and no new mnemonic collides with
a v1 board key, so the bridged US-USD reading keeps exactly its inputs and its
restriction. The series appear only as source-dated, cited rows: on the
`/api/v2/money-markets` atlas metrics and legal notices and on the
`/api/v2/markets/US-USD/series` instrument references and observations. No
website panel or MCP tool is re-pointed to them in this stage. Every value
carries its Board citation and
`https://www.federalreserve.gov/disclaimer.htm`; the H.15 citation keeps
"Treasury constant maturities: U.S. Treasury", the source its footnote names.
The H.10 indexes are labelled "JAN06=100" on the release while the package's
`UNIT` attribute still reads `Index:_1997_Jan_100`; the attribute is pinned as
published.

H.10 posts the previous week's daily rates on Mondays at 16:15 New York. The
adapter clock (next business day, 16:15) is the earliest a row could be public:
exact for Friday rows and earlier than the weekly posting for the others,
never a receipt; knowledge time is always the actual capture. Currentness uses
an instrument clock of five business days at 16:15, so the day after the last
published row falls due at the next Monday posting.

The H.15 commercial paper rates (`RIFSPPNAAD90_N.B`, `RIFSPPFAAD90_N.B`) are
parsed by the held package `fed_h15_cp` from the same H.15 file but carry a
code-level rights hold: the gate stays closed even if a pack declared them, the
US pack declares nothing for them, and registry record
`frb_h15_commercial_paper` is `metadata_only` and `policy_blocked` pending the
owner's decision on the DTCC provenance. Their test fixture values are
synthetic.

Registry records: `frb_prates_release`, `frb_h41_release` (extended),
`frb_h15_release`, `frb_h15_commercial_paper`, `frb_h10_release` and
`frb_g17_release`. G.17's annual revision on 2026-11-24 rebases the index to
2022, which changes the pinned `UNIT` and faults `fed_g17` closed by design
until the pin is reviewed. After release, a one-off
`seiche market-backfill --market US-USD` gives the new series history; IOER
rows never fall in a routine window.

### US direct policy settings from the NY Fed (0.17.0 candidate, Stage 2b)

The existing NY Fed Markets API adapters and terms also serve two policy
settings as context. `nyfed_unsecured_rates` reads the FOMC federal funds
target range that every EFFR row reports (`targetRateFrom`, `targetRateTo`) as
`US.NYFED.FFR_TARGET_RANGE_LOWER` and `_UPPER`, the numbers FRED republishes
as DFEDTARL and DFEDTARU. A row without either bound carries no range; one
bound alone, an inverted range or one wider than 100 bp fails the run closed.
The evidence keeps only the date and the bound, so a revised rate or volume on
the same row is not a revised range. The range is parsed apart from the
distribution, so the signed NY Fed history archive keeps its fixed
instruments. `nyfed_facilities` reads the Standing Repo Facility rate
(`US.NYFED.SRF_RATE`) from the overnight repo results: `percentOfferingRate`
under full allotment (from 2025-12-11) and `minimumBidRate` in the earlier
multiple-price auctions. A date is published only when every overnight
operation states the same rate; term operations and repos before the SRF's
first operation on 2021-07-29 are excluded.

Their roles (`POLICY_RANGE_LOWER`, `POLICY_RANGE_UPPER`,
`STANDING_REPO_RATE`) are not policy-anchor roles and no capability reads
them, so IORB stays the atlas anchor and the SOFR minus IORB spread is
unchanged even though IORB's publication clock is unknown. The FRED copy
(`US.FED.POLICY_CEILING`, DFEDTARU) stays metadata-only, and the
`us_policy_floor` gap stays open.

## Deliberate trade-offs and validation boundary

- v1 stays US-centred until parity migration completes. That preserves users but
  leaves request-time collection on legacy routes temporarily.
- Dated settlement calendars are bounded and source-labelled. Mainland China
  includes official working-weekend overrides through 2026 and fails closed for
  2027 because the official annual schedule has not yet been published. The
  calendar gate stays pending until that notice is reviewed. Other reference
  calendars are declared through 2035.
- Reference mappings are discoverable before support. Their status and missing
  capabilities prevent discovery from being mistaken for validated coverage.
- Local scores blend a declared market calibration with point-in-time
  within-market percentile/robust-z normalization once minimum history accrues.
  Raw interest-rate levels never cross markets.
- Forward records are immutable per-product, per-calibration-generation hash
  graphs. Verification follows authenticated parent hashes rather than
  timestamp order and fails on forks, orphans, cycles, missing predecessors,
  or non-unique roots/heads. Historical generations remain reportable but
  cannot satisfy the active generation's promotion gate. They establish the
  paper trail but do not, by themselves, promote a pack.

NZ-NZD uses `nz-nzd-local-forward-v2` for evidence captured after the
2026-08-11 topology incident. Its v1 gauge and overview rows remain immutable
historical incident evidence; they are explicitly quarantined from v2 maturity
counts and are never repaired, relinked, or represented as passing. See the
[forward-chain incident runbook](FORWARD_CHAIN_INCIDENT_2026-08-11.md) for the
production migration boundary.

HK-HKD retains one audited v1 gauge record whose committed JSON preimage used
`-0.0` before PostgreSQL `JSONB` returned it as `0.0`. Validation policy v3
recognizes only that exact identity and preimage reconstruction; new writers
normalize signed zero before hashing. No row is rewritten or broadly exempted.
See the [payload-canonicalization incident
runbook](FORWARD_PAYLOAD_CANONICALIZATION_INCIDENT_2026-08-14.md).

No pack is promoted to `SUPPORTED` by this deployment. Promotion still requires
all eleven evidence-backed checks: units/schema, calendar/timezone, truncation,
extra lag, revision leakage, label shuffle, missing-source injection, local
temporal holdout, leave-one-market-out, forward paper record, and US parity.
NZ remains explicitly source-unavailable until RBNZ supplies written automated
access approval or an entitled licensed/tenant source is configured. Even then,
source access alone is not a promotion decision. Global Tide remains null until
at least two aligned, usable `FX_SWAP_BASIS` histories are available; public
forward points or premia are not silently relabelled as covered-interest-parity
basis.

The remaining work is real forward evidence accrual, labelled holdout and
shuffle corpora, leave-one-market-out and calibration review, dated calendar
maintenance, US parity evidence, RBNZ access approval, and eligible
cross-currency basis history. Promote packs individually only after their
artifacts pass; only then update the public claim to “market-pack agnostic.”

Revisit partition compaction, queueing, and partial pooling only after observed
collector volume and leave-one-market-out validation justify the complexity.
