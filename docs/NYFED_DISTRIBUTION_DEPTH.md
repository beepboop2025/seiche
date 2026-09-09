# NY Fed reference-rate distributions

Seiche's existing `nyfed_rates` and `nyfed_unsecured_rates` collectors now
normalize all five published distribution statistics and transaction volume
for SOFR, TGCR, BGCR, EFFR and OBFR. This adds 23 canonical instruments:
30 distribution instruments plus the four existing SOFR averages/index series.

| Source field | Instrument suffix | Canonical role | Conversion |
| --- | --- | --- | --- |
| `percentPercentile1` | `P01` | `RATE_P01` | percent × 100 = basis points |
| `percentPercentile25` | `P25` | `RATE_P25` | percent × 100 = basis points |
| `percentRate` | `MEDIAN` | `RATE_MEDIAN` | percent × 100 = basis points |
| `percentPercentile75` | `P75` | `RATE_P75` | percent × 100 = basis points |
| `percentPercentile99` | `P99` | `RATE_P99` | percent × 100 = basis points |
| `volumeInBillions` for SOFR/TGCR/BGCR | `VOLUME` | `REPO_VOLUME` | USD billions × 1,000 = USD millions |
| `volumeInBillions` for EFFR/OBFR | `VOLUME` | `UNSECURED_FUNDING_VOLUME` | USD billions × 1,000 = USD millions |

Instrument IDs are `US.NYFED.<benchmark>_<suffix>`. These are daily published
transaction aggregates, not executable prices or individual trade records.
The benchmarks cover overlapping transaction sets: never add their volumes to
estimate total market size. The published benchmark is the volume-weighted
median; no other percentile substitutes for it. Missing fields remain absent.

The collectors use the NY Fed's official
[secured search endpoint](https://markets.newyorkfed.org/api/rates/secured/all/search.json)
and [unsecured search endpoint](https://markets.newyorkfed.org/api/rates/unsecured/all/search.json),
with explicit `startDate` and `endDate` bounds. The scheduled calls already
download these fields, so the expansion requires no additional source request
or scheduler. Existing daily collection fills the rolling 45-day window,
recomputed for each fetch even when the worker remains running overnight.
Each request batch uses one interval; explicit historical backfills retain
their original cutoff. Older series require a separately validated historical
load, including publication calendars and the pre-2016 EFFR methodology boundary.

Source event dates, captured raw response bytes, row evidence hashes, revision
indicators and per-row knowledge times remain available. Existing SOFR lineage
is unchanged. Pack clocks use the following business day at 08:00 New York
time for secured benchmarks and 09:00 for unsecured benchmarks; these are
scheduled publication times, not recovered historical posting timestamps.
SOFR averages/index keep their separate same-day value date and 08:00 clock.
Backfills describe current-vintage historical observations, not proof that
those values were known on their event date. See the NY Fed's
[methodology and publication/revision information](https://www.newyorkfed.org/markets/reference-rates/additional-information-about-reference-rates).

At 2026-09-09 07:05 UTC, a read-only live probe for 2026-07-26 through
2026-09-09 returned HTTP 200 from both endpoints: 121 secured source rows
produced 664 observations across 22 instruments; 60 unsecured source rows
produced 360 observations across 12 instruments. Combined: 1,024 observations
across 34 instruments. Each distribution instrument had 30 business dates;
the four SOFR averages/index series each had 31. The latest benchmark event
date was September 4 while SOFR averages/index extended through September 8.
This difference is preserved. The probe does not establish production
deployment or a completed historical import.

Source: Federal Reserve Bank of New York. These data remain subject to the
NY Fed's [Terms of Use](https://www.newyorkfed.org/privacy/termsofuse), including
required attribution and republication notices. Public displays/exports must
retain source URLs and identifiers, disclose Seiche's unit conversions, and
carry the applicable reference-rate republication notice. The NY Fed does
not endorse Seiche or accept responsibility for Seiche's republication or use
of these data. Access and reuse permission do not imply public-domain status.
