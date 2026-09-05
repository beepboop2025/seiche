---
pretty_name: Seiche Audited Direct-OFR Research Snapshot
language:
- en
license: other
license_name: us-government-work-ofr-credit-requested
license_link: https://www.financialresearch.gov/legal-notices/
tags:
- finance
- economics
- time-series
- repo-markets
- money-market-funds
- point-in-time-data
size_categories:
- 10K<n<100K
configs:
- config_name: default
  data_files:
  - split: full
    path: data/*.csv
dataset_info:
  features:
  - name: entity
    dtype: string
  - name: variable
    dtype: string
  - name: date
    dtype: date32
  - name: value
    dtype: float64
  - name: unit
    dtype: string
  - name: measurementMethod
    dtype: string
  - name: observationPeriod
    dtype: string
  splits:
  - name: full
    num_examples: 11163
---

# Seiche audited direct-OFR research snapshot

> **Dataset identity: rights-reviewed direct-OFR snapshot. No DOI has been
> assigned. Public listing status is receipt-tracked in Seiche's
> [distribution ledger](https://github.com/beepboop2025/seiche/blob/v0.12.2/distribution/submissions.csv).**

This dataset contains 11,163 observations from ten series obtained
directly from the United States Office of Financial Research Short-term Funding
Monitor API: six repo-market series and four money-market-fund series. It is a
rights-reviewed direct-OFR research snapshot, not a mirror of Seiche's live runtime database.

The package deliberately excludes every value fetched through FRED, New York
Fed reference-rate rows with additional terms, the semantically mismatched
primary-dealer row, licensed/restricted series, and all Seiche-derived outputs.
The exact inclusion and exclusion review is recorded in
[`integrations/datacommons/RIGHTS_AND_SOURCES.md`](https://github.com/beepboop2025/seiche/blob/v0.12.2/integrations/datacommons/RIGHTS_AND_SOURCES.md).

## Data contract

The `full` split contains two CSV resources:

| Resource | Series | Rows | SHA-256 |
|---|---:|---:|---|
| `ofr_repo_markets.csv` | 6 | 10,489 | `307ae6ad5bbe8653c3bd4abf63449d4229b0fbba1ee66014d91f813e866d3a4a` |
| `ofr_money_market_funds.csv` | 4 | 674 | `1d0975b69dcb6f3465e957679b21bacae02695145b1e77563dae3258d8b524cd` |

Each row contains `entity`, `variable`, `date`, `value`, `unit`,
`measurementMethod`, and `observationPeriod`. Dates are source observation
dates, not retrieval or notebook execution times. Daily preliminary repo data
and monthly revised MMF data retain their native publication clocks and must
not be forward-filled into a common clock.

## Sources and rights boundary

- [OFR Short-term Funding Monitor API](https://www.financialresearch.gov/short-term-funding-monitor/api/)
- [OFR Repo Markets release](https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/)
- [OFR Money Market Fund release](https://www.financialresearch.gov/short-term-funding-monitor/datasets/mmf/)
- [OFR legal notices](https://www.financialresearch.gov/legal-notices/)

OFR describes the interface as open for public use. Its legal notice explains
the federal-work copyright boundary, requests credit, and warns that separately
copyrighted material still requires permission. The ten admitted series had an
empty upstream rights-description field at the pinned build. This is an
engineering rights review, not legal advice or an OFR endorsement.

## Intended use and limitations

Suitable uses include reproducible descriptive analysis, data-engineering
examples, and research on secured funding and money-fund plumbing. This snapshot
is not real-time, exhaustive, investment advice, a trade signal, or a claim of
Data Commons, Hugging Face, or peer-review acceptance. Users must preserve
source attribution, series-specific units, preliminary/revised status, missing
values, and native clocks.
