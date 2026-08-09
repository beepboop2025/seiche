# Seiche market-pack architecture

Status: migration foundation. The public product must not claim broad market
support while packs remain `REFERENCE` or `VALIDATING`.

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

Rows are append-only across revisions. An as-of query first filters by knowledge
cutoff, then selects the latest knowable revision for each instrument/event pair.
Legacy `Series.fetched_at` remains for v1 compatibility but is not eligible for
canonical historical prediction.

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

Production should mount the raw and normalized roots on the Hetzner data volume.
Local development uses SQLite for compatibility. Setting
`SEICHE_DATABASE_URL` selects the PostgreSQL repository for canonical
observation metadata and latest-snapshot indexes without changing adapters or
API contracts; install the `postgres` optional dependency on that service.

The v2 API never invokes collection. It reads only canonical observations and
sealed snapshots. During migration, the completed v1 US cycle materializes
`US-USD` overview/gauge products after assembly; a materializer failure is logged
and cannot block v1 publication.

## Product contracts

- Local Seiche Gauge: one monetary area, local calibration, explicit missing
  capabilities and stale/faulted inputs.
- Global Seiche Tide: cross-basin transmission only. It never averages local
  gauge values. Until eligible cross-basin evidence exists, its value is
  `UNAVAILABLE`/`null`.

Licensed and tenant inputs may be used in permitted calculations while v2 series
responses redact values whose redistribution policy is not `allowed`.

## Deliberate trade-offs

- v1 stays US-centred until parity migration completes. That preserves users but
  leaves request-time collection on legacy routes temporarily.
- Annual calendars with working weekends fail closed when a dated schedule has
  not been loaded. Continuity is lower, but a weekday is never silently treated
  as a settlement day.
- Reference mappings are discoverable before support. Their status and missing
  capabilities prevent discovery from being mistaken for validated coverage.
- Aggregate publication policy for a partly covered local gauge remains an
  explicit product decision in `seiche/markets/publication.py`; the safe default
  withholds the aggregate while still allowing component-level evidence.

## Next production slices

1. Implement canonical adapters and accrue forward observations, beginning with
   the US parity pack and official EUR/JPY/INR sources.
2. Run the supervisor as a separate service and configure Hetzner capture roots.
3. Deploy the PostgreSQL schema in production and materialize snapshots when
   inputs change.
4. Complete per-pack calendars, local calibration, holdouts, and forward records.
5. Promote packs individually; only then update the public market-pack claim.

Revisit partition compaction, queueing, and partial pooling only after observed
collector volume and leave-one-market-out validation justify the complexity.
