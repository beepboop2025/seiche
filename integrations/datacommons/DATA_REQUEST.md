# Data Commons data request draft

Do not submit this text as an attestation without owner review. The official
form requires a Google sign-in, and contributions may require a Google CLA.

## Title

OFR short-term funding market observations curated by Seiche

## Request

Please review a proposed import of ten United States short-term funding market
series curated by Seiche from the official Office of Financial Research (OFR)
Short-term Funding Monitor API.

The first provenance contains six OFR U.S. Repo Markets series: total DVP and
tri-party repo transaction volumes, DVP and tri-party overnight/open
volume-weighted mean rates, and GCF overnight/open rate and volume. The second
contains four OFR U.S. Money Market Fund series: total investments, total repo
investments, repo cleared by FICC, and repo with the Federal Reserve.

All observations are associated with `country/USA`. Repo series are daily and
preliminary. Money-market-fund series are month-end observations whose complete
history may be revised with monthly releases. Dollar observations remain in raw
U.S. dollars; rates remain in percent. Missing source values are omitted rather
than imputed. The package does not contain a global score, institution ratings,
licensed market data, investment advice, or forecasts.

## Why this fills a gap

These observations make wholesale funding capacity, repo-market activity, and
the cash-provider side of the U.S. money market directly queryable by place and
date. On 2026-08-21, Data Commons indicator-resolution searches for the exact
OFR concepts did not return close OFR repo or money-market-fund variables. Please
perform the authoritative duplicate and StatVar review before accepting any new
DCIDs.

## Source and provenance

- Source organization: Office of Financial Research, U.S. Department of the
  Treasury
- Official API: <https://data.financialresearch.gov/v1/series/full>
- Repo release: <https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/>
- Money-market-fund release: <https://www.financialresearch.gov/short-term-funding-monitor/datasets/mmf/>
- Curator and public methodology context: <https://seiche.info/money-markets/>
- Reproducible mapping and build package:
  <https://github.com/beepboop2025/seiche/tree/main/integrations/datacommons>

The build downloads directly from OFR, not from FRED or a Seiche cache. It pins
the exact release, series name, unit, frequency, observation period, vintage,
and empty OFR rights field for every admitted series, then fails closed on
metadata drift.

## Rights review basis, not an owner attestation

OFR describes its API as an open interface for public use. OFR's legal notice
says copyright may not be claimed in works created by federal employees in the
course of duty, requests credit, and reserves separately copyrighted material
to its owner. The selected ten series are OFR releases and currently carry an
empty `metadata.rights.description` field. Please confirm that this basis and
the proposed OFR attribution satisfy base Data Commons requirements before
ingestion.

No FRED-fetched values are included because current FRED terms prohibit AI use
and database incorporation. No New York Fed reference-rate values are included
because their required downstream notices and conditions need a separate
compatibility review. No Seiche composite values are included because AGPL-3.0-or-later
licenses code, not the output dataset.

## Refresh and quality contract

The reproducible builder can refresh the two variable-per-row CSVs from the OFR
API. It rejects a source metadata change before replacing output, retains native
daily or monthly dates, emits no null sentinel, and validates unique
entity-variable-date keys, numeric finite values, units, methods, manifests,
and file hashes offline.

## Owner-only confirmations before submission

The owner must independently confirm all of the following in the official form:

1. The applicant has authority to make the request and provide the package.
2. The identified rights basis is accurate and sufficient.
3. Any required individual or corporate Google CLA has been handled.
4. The contact information and ongoing refresh commitment are correct.
5. Data Commons may modify or replace the proposed StatVar mapping after review.

No confirmation above was made by the automation that prepared this draft.
