# Original-publisher USD source qualification — 2026-10-28

This change keeps acquisition identities separate. FRED-backed US.NYFED.SOFR,
US.NYFED.EFFR and US.FED.IORB remain subject to the FRED publication policy.
No existing rows are relabelled or backdated.

## NY Fed SOFR benchmark

The existing direct adapter retrieves the NY Fed Markets API secured reference
rates. The parser maps the publisher's percentRate field to
US.NYFED.SOFR_MEDIAN; this is published SOFR, the transaction-volume-weighted
median, not the 25th percentile. The atlas now selects this existing instrument
as the USD benchmark without changing its ID, source, revision, evidence hash,
dates or status. TGCR/BGCR median observations are not substituted for SOFR.

Sources: https://markets.newyorkfed.org/api/rates/secured/all/search.json
and https://www.newyorkfed.org/privacy/termsofuse.html.
The existing direct EFFR instrument remains US.NYFED.EFFR_MEDIAN, supplied by
the NY Fed unsecured-rates API. Historical/current FRED copies are distinct.

## Federal Reserve Board IORB

The new fed_prates adapter retrieves only
https://www.federalreserve.gov/releases/prates/data/FRB_PRATES_xml.zip.
It extracts PRATES_data.xml series RESBM_N.D, available OBS_VALUE observations,
requiring the publisher's Percent/UNIT_MULT=1 declarations. The canonical
instrument is US.FED.IORB_DIRECT; percent values are normalized to basis points
by the existing pack contract. The raw ZIP and stable row evidence retain
separate hashes. New rows enter Seiche's knowledge state at actual acquisition.

The XML supplies effective observation dates, not individual publication
receipts. published_at stays null, publication precision stays unknown, and
the atlas does not report fresh publication from an invented schedule.
Historical acquisition is current-vintage history, not an archived PIT record.
Recent collection retains the requested 45-day window; explicit backfill uses
the configured start date. Future effective observations are excluded.

The Board's notice https://www.federalreserve.gov/disclaimer.htm states that
Board information may be copied and distributed unless otherwise indicated,
with source citation; identified non-Board materials have separate rights.
The atlas retains attribution, the source/terms URLs, and the distinction
between Seiche calculations and official Board material.

Live qualification at 2026-10-28T12:10:53Z fetched 55,554 bytes, SHA-256
f64e0a4a194a0a890fdbdd31110ea09958d2f81716ade9eab645b15ba3a6d1cf.
The current file contained 1,918 available IORB dates from 2021-07-29 through
2026-10-28. The bounded recent adapter emitted 46 dates; every knowledge time
equalled actual capture and every source publication time remained unknown.
This source check did not activate a deployment or modify a production store.

Validation covers changed units, duplicates, nonfinite values, XML declarations,
fixed-origin/size-limited download, raw-byte identity, future exclusion, and
knowledge-cutoff filtering. The source test is included in market-platform CI.

## Remaining boundaries

This adapter and benchmark selection do not make legacy assembled money-desk,
FX, composite, forecast or backtest outputs rights-qualified. Those paths have
separate FRED dependencies and require explicit public projections. Existing
publication/freshness/common-horizon checks remain applicable; an unavailable
or overdue reference cannot become ready merely through a source change.
