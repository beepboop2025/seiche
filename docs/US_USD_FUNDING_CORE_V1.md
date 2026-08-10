# US-USD funding-core profile v1

`us-usd-funding-core-v1` is Seiche's pinned raw-input profile for the USD
funding-core research track. The corresponding planned Lab model ID is
`us-usd-funding-core-var1-v1`; Seiche does not fit or serve that model.

The profile exports exactly these canonical instruments:

| State | Instrument | Role | Canonical semantics |
|---|---|---|---|
| `sofr_median_bp` | `US.NYFED.SOFR_MEDIAN` | `RATE_MEDIAN` | basis points, simple, ACT/360 |
| `sofr_p99_bp` | `US.NYFED.SOFR_P99` | `RATE_P99` | basis points, simple, ACT/360 |
| `sofr_volume_usd_m` | `US.NYFED.SOFR_VOLUME` | `REPO_VOLUME` | USD millions; no rate convention |

Instrument identity is part of the versioned contract. Repository rows are
filtered to those three IDs before the generic role builder runs, so a future
instrument assigned the same semantic role cannot silently replace a training
input.

## NY Fed field correction and lineage

The NY Fed secured-rates API's `percentRate` field is the published SOFR
median. An earlier canonical parser incorrectly mapped
`percentPercentile25` into `US.NYFED.SOFR_MEDIAN`. The parser now maps
`percentRate` and gives every emitted value an explicit revision ID of the
form:

```text
nyfed:<source-field>:<effective-date>:<revision-indicator-or-unrevised>-<content-prefix>
```

No historical row is deleted or rewritten. On recollection, a former
P25-derived median remains the earlier revision and the corrected
`percentRate` observation becomes the later revision for that event. Legacy
hash-only P99 and volume rows likewise gain one provenance-only successor that
binds `percentPercentile99` or `volumeInBillions`, even when the value bytes are
unchanged. The content prefix is derived from the canonical source-row
evidence, so an unflagged upstream correction cannot collide with the prior
revision ID. When content changes, canonical ingestion also appends the real
capture occurrence to the revision ID; this keeps an A-to-B-to-A content
reversion unique without creating rows for identical re-fetches. The profile
fails closed until every latest median, P99, and volume revision explicitly
binds its expected source field and exact event date. All revisions for dates
admitted to the profile remain in the exported pack.

"Latest" is evaluated strictly at the requested `as_of`: rows whose event,
source-publication, or knowledge clock follows the cutoff are excluded before
the lineage gate and event intersection. A future corrected capture can never
make an earlier P25-derived latest-as-of row look eligible.

## Alignment and history floor

The event grid is the exact timestamp intersection of the latest usable
median, P99 and volume observations. A date missing any one input is omitted.
There is no forward fill, backward fill, interpolation, resampling or other
imputation. At least 504 complete dates are required; otherwise export fails
with an insufficient-history error.

The output uses `seiche.world-model-input-pack.v1`, whose policy remains a
retrospective research export: `imputation=forbidden`,
`forward_evidence_eligible=false`, `can_publish=false`, and
`can_execute=false`. It is a training/input artifact, not a public/raw API,
forecast or execution surface.

## Retrospective versus forward evidence

The one-time historical recollection imports the source's current historical
view. Those rows become knowable to Seiche when that capture occurs; they are
not evidence of what the file contained at an earlier decision time and must
not be used as first-release outcome labels. Their value is retrospective
training and revision analysis.

Forward evidence can begin only with live post-fix captures recorded after the
corrected parser is deployed. The first qualifying knowledge timestamp is a
property of those append-only rows, not this document, the export file's
`as_of`, or the historical effective date. External anchoring or a later
forward-record process may strengthen that evidence boundary; this exporter
does not manufacture it.

## Operation

After a completed collection cycle containing any `US-USD` adapter, the market
worker makes one export attempt at the cycle boundary. It never exports once
per adapter. The target directory is configured with
`SEICHE_USD_FUNDING_CORE_EXPORT_DIR`; production provisioning defaults it to:

```text
/var/lib/seiche/exports/us-usd-funding-core-v1
```

The stable file is `us-usd-funding-core-v1.json`. Serialization is canonical
UTF-8 JSON and replacement is atomic after file and directory fsync. A profile,
lineage or filesystem failure is logged and surfaced in one-shot collection
results without changing any sibling collector's completed status.

The corrected historical import has its own one-time backfill generation:
`US-USD--nyfed_rates--funding-field-lineage-v3.done`. A host's older unversioned
`US-USD--nyfed_rates.done` marker does not suppress this correction run. Once
the full three-field recollection and funding-core export both succeed, the
v3 marker is written and
future backfill-service starts skip it normally. Markers for every other
adapter retain their existing names and behavior; provisioning does not delete
or reset them.
