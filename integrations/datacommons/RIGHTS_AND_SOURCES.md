# Rights and source audit

Audit date: 2026-08-21. This is an engineering readiness review, not legal
advice and not an owner attestation.

## Contribution contract

Data Commons says it welcomes public data, asks prospective providers to file
a data request in Google Issue Tracker, requires review, and requires a Google
Contributor License Agreement for contributions. The request page requires a
Google sign-in. No request or CLA action was taken during this work.

- Contribution guide: <https://docs.datacommons.org/contributing/>
- Data request form: <https://issuetracker.google.com/issues/new?component=1660823&template=2053232>
- Data model: <https://docs.datacommons.org/data_model.html>
- Observation and MCF format: <https://docs.datacommons.org/custom_dc/custom_data.html>
- Config format: <https://docs.datacommons.org/custom_dc/config.html>

The supplied import uses Data Commons' current variable-per-row CSV contract,
one entity type per file, numeric observations, MCF StatisticalVariable nodes,
and explicit source/provenance mappings. Proposed variable definitions should
still be reviewed against existing Data Commons variables before acceptance;
the official guidance prioritizes reuse to prevent misleading duplicates.

## Candidate-eligible tranche: ten OFR-produced series

The OFR describes its STFM API as an open interface for public use that needs no
token or registration. Its legal notice says no copyright may be claimed in a
work created by a federal employee in the course of duty, requests credit, and
warns that separately copyrighted material still needs permission. The ten
admitted series all have an empty `metadata.rights.description` field in the
official API. The build fails if that field becomes non-empty or if source,
release, name, unit, frequency, observation period, or vintage metadata drifts.

- OFR API: <https://www.financialresearch.gov/short-term-funding-monitor/api/>
- OFR legal notice: <https://www.financialresearch.gov/legal-notices/>
- OFR repo release: <https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/>
- OFR money-market-fund release: <https://www.financialresearch.gov/short-term-funding-monitor/datasets/mmf/>
- OFR MMF methodology: <https://www.financialresearch.gov/short-term-funding-monitor/files/methodologies/2020-04--Methodology-MMF.pdf>

This basis supports a reviewed data request, not a claim that Google has
accepted the import or that the operator has executed the CLA. The request asks
Data Commons to confirm that the ten proposed variables are new and appropriate
and to confirm the source-rights interpretation before ingestion.

## Excluded sources

### FRED-backed values

The live Seiche series index exposes 81 of 105 configured series for bulk
download: 68 fetched through FRED and 13 through OFR. The current FRED terms
expressly prohibit using FRED services or content in connection with machine
learning or AI, and prohibit storing, caching, archiving, or incorporating FRED
content into another database. Because base Data Commons is an AI-accessible
database, no value fetched through FRED is included here, even when the original
authority may publish the underlying work in the public domain.

- FRED terms: <https://fred.stlouisfed.org/legal/terms/>
- Federal Reserve Board public-domain notice, useful only for future direct
  re-sourcing: <https://www.federalreserve.gov/disclaimer.htm>

The safe remediation is direct collection from the original authority followed
by a fresh rights audit. It is not copying the value already fetched by Seiche
from FRED.

### New York Fed reference rates

BGCR and TGCR are available through OFR, but the OFR metadata carries the New
York Fed terms. Those terms generally permit copying, distribution, storage,
and derivatives, while requiring source notices, a reference-rate disclaimer,
non-endorsement language, and distribution under the same permissions and
conditions. The terms also identify DTCC-licensed input data for BGCR. These two
series stay outside the package until Data Commons confirms its presentation
and redistribution layers can preserve every applicable condition.

- OFR reference-rate release: <https://www.financialresearch.gov/short-term-funding-monitor/datasets/fnyr/>
- New York Fed terms: <https://www.newyorkfed.org/privacy/termsofuse>

### Primary-dealer row

Seiche config maps `PD_FIN_TOT` to upstream mnemonic
`NYPD-PD_AFtD_TOT-A` and labels it primary-dealer financing total. OFR's live
metadata identifies that mnemonic as Primary Dealer Aggregate Fails to Deliver:
Total. No observation or proposed variable is prepared until the mapping is
corrected and independently reviewed.

### Seiche-derived and global outputs

AGPL-3.0 covers Seiche code, not its upstream data. There is no explicit output
data license that grants Data Commons redistribution rights for the Seiche
composite or engine outputs, and principal derived products combine multiple
source contracts. The global money-market catalog dated 2026-08-21 also reports
zero supported and zero evidence-eligible packs. Neither category is included.

- Seiche rights boundary: <https://seiche.info/terms>
- Seiche global catalog: <https://seiche.info/money-markets/catalog.json>
- Seiche public series index: <https://api.seiche.info/api/series/index.json>

## Current conclusion

Ten OFR-produced series are candidate-eligible for a Data Commons review. Zero
Seiche-derived series are ready, zero global market packs are evidence-eligible,
and zero datasets have been submitted or accepted. The owner-gated action is to
review this rights basis, confirm authority, complete any required CLA step, and
file the prepared data request. No owner statement should claim more than that.
