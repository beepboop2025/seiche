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
API contract. The dedicated `market-platform-ci` workflow executes the complete
repository contract against PostgreSQL 17.

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
universal semantic engines through versioned `*-local-forward-v1` calibrations.
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

As of 2026-08-09, live sandbox backfill tests complete for 19 of 21 official
adapter schedules across USD, EUR, GBP, JPY, CNY, HKD, INR, AUD, and SGD. Both
RBNZ schedules receive a source-side Cloudflare 403 from this collector
environment. They fail independently, persist scoped faults, and seal NZD as
`UNAVAILABLE`; Seiche does not relabel RBNZ's mixed secured/unsecured overnight
cash rate to manufacture a result.

Licensed and tenant adapters remain declarations until an entitled deployment
supplies credentials/data. Their absence is a capability state, never a zero.

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
- Forward records are immutable per-product hash chains. They establish the
  paper trail but do not, by themselves, promote a pack.

No pack is promoted to `SUPPORTED` by this deployment. Promotion still requires
all eleven evidence-backed checks: units/schema, calendar/timezone, truncation,
extra lag, revision leakage, label shuffle, missing-source injection, local
temporal holdout, leave-one-market-out, forward paper record, and US parity.
NZ remains explicitly unavailable unless RBNZ access is resolved or an entitled
licensed/tenant source is supplied. Global Tide remains null until at least two
aligned, usable `FX_SWAP_BASIS` histories are available; public forward points
or premia are not silently relabelled as covered-interest-parity basis.

The remaining work is real forward evidence accrual, labelled holdout and
shuffle corpora, leave-one-market-out and calibration review, dated calendar
maintenance, US parity evidence, RBNZ access, and eligible cross-currency basis
history. Promote packs individually only after their artifacts pass; only then
update the public claim to “market-pack agnostic.”

Revisit partition compaction, queueing, and partial pooling only after observed
collector volume and leave-one-market-out validation justify the complexity.
