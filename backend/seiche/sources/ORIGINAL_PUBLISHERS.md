# Qualified original publisher captures

`original_publishers.py` combines the 49 Board aliases described in
`FED_RELEASES.md` with eight exact BLS, BEA, ECB and EIA statistics. The 57
aliases represent 53 distinct publisher series, all previously restricted in
the 149-row legacy registry. They become exportable only after an independently
acquired, validated capture exists. Existing proprietary and mixed-source
product gates remain unchanged.

```sh
SEICHE_RUNTIME_DATA_DIR=/absolute/runtime/data \
  python -m seiche.sources.original_publishers --receipt /absolute/qualification.json
```

This bounded collector uses public endpoints without credentials or payment.
It neither deploys nor publishes. Normal legacy assembler entry points also
dispatch these exact aliases to the qualified adapters. No legacy observations
are relabelled, copied into the qualified namespace, or used after a failed
refresh. Previous qualified captures retain their original retrieval clocks.

The eight added aliases are:

| Aliases | Publisher identity and transformation | Reuse evidence |
| --- | --- | --- |
| CORE_CPI, ENERGY_CPI | BLS CUSR0000SA0L1E and CUSR0000SA0E; seasonally adjusted monthly indexes, 1982–84=100; month-start reference labels | [BLS reuse](https://www.bls.gov/bls/linksite.htm) |
| GDP | BEA NIPA Table 1.1.5, A191RC, quarterly current-dollar GDP at SAAR; millions USD divided by 1000 for the registry's billions USD | [BEA reuse](https://www.bea.gov/help/faq/147) |
| ESTR | ECB EST/B.EU000A2X2A25.WT, percent, effective dates unchanged | [ECB disclaimer](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) |
| ECB_DFR | ECB FM/D.U2.EUR.4F.KR.DFR.LEV; percent per annum, effective dates unchanged | Same ECB terms |
| ECB_ASSETS | ECB ILM/W.U2.C.T000000.Z5.Z01; Eurosystem changing composition, millions EUR; ISO week labelled Friday | Same ECB terms |
| CUSHING_STOCKS, CRUDE_STOCKS_EX_SPR | EIA W_EPC0_SAX_YCUOK_MBBL and WCESTUS1; weekly ending stocks excluding SPR, thousand barrels | [EIA reuse](https://www.eia.gov/about/copyrights_reuse.php) |

ECB exports carry the free-source and modification notices. These permissions
do not qualify unrelated third-party ECB data or EIA commodity vendor prices.
The BEA workbook parser pins the exact worksheet, line, units and series code;
the free API's registration requirement does not apply to this published XLSX.
BLS uses ten-year batches and a daily cache, within the keyless API limits.
The actual October 2025 CPI missing observations carry the publisher's X
footnote about the lapse in appropriations. JSON retains explicit nulls and CSV
empty values; neither exporter imputes them. Native Fed missing statuses are
also preserved.

All captures are the current publisher vintage. `knowledge_time` is acquisition,
not historical availability. Actual publication time is unknown. Where BEA or
EIA supplies a publication date it is separately retained, without inventing
a time. Source observation/reference dates remain separate from both clocks.

For a populated isolated qualification database, the original registry changes
from 50 to 107 permitted raw-export aliases and 99 to 42 restricted aliases.
This is capture eligibility, not deployed availability or proof that mixed-source
engines can publish. A cold runtime still restricts the 57 until its own collector
acquires them. NYFed, SONIA and OECD overnight candidates remain unqualified.
