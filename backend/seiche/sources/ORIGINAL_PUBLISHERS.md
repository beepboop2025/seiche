# Qualified original publisher captures

`original_publishers.py` combines the 49 Board aliases described in
`FED_RELEASES.md` with 15 exact BLS, BEA, ECB, EIA, NY Fed, Bank of England
and Bank of Japan statistics. The 64 aliases represent 59 distinct publisher
series. Of the legacy registry's 82 FRED aliases (76 unique FRED IDs), 61
aliases and 56 FRED IDs now come from their original publishers; the other
three aliases (ESTR, CUSHING_STOCKS, CRUDE_STOCKS_EX_SPR) were already direct
and are re-qualified here. An alias becomes exportable only after an
independently acquired, validated capture exists and its redistribution
status is `allowed`.

The 20 FRED IDs that stay on FRED, and therefore stay withheld, are the 17
blocked series (VIXCLS, BAMLH0A0HYM2, BAMLC0A0CM, SP500, NASDAQCOM,
DCOILWTICO, DCOILBRENTEU, DHHNGSP, the seven IMF commodity series, TEDRATE and
NFCI) and the three OECD call rates (IRSTCI01INM156N, IRSTCI01JPM156N,
IRSTCI01KRM156N), whose OECD switch is separate work.

```sh
SEICHE_RUNTIME_DATA_DIR=/absolute/runtime/data \
  python -m seiche.sources.original_publishers --receipt /absolute/qualification.json
```

This bounded collector uses public endpoints without credentials or payment.
It neither deploys nor publishes. Normal legacy assembler entry points also
dispatch these exact aliases to the qualified adapters, so the SOFR minus
IORB spread, the money desk and every engine read the direct captures. No
legacy observations are relabelled, copied into the qualified namespace, or
used after a failed refresh. Previous qualified captures retain their
original retrieval clocks.

The 15 statistics aliases are:

| Aliases | Publisher identity and transformation | Reuse evidence |
| --- | --- | --- |
| CORE_CPI, ENERGY_CPI | BLS CUSR0000SA0L1E and CUSR0000SA0E; seasonally adjusted monthly indexes, 1982-84=100; month-start reference labels | [BLS API Terms of Service](https://www.bls.gov/developers/termsOfService.htm), accepted by owner decision `seiche.source-use.2026-11-08.bls-api` |
| GDP | BEA NIPA Table 1.1.5, A191RC, quarterly current-dollar GDP at SAAR; millions USD divided by 1000 for the registry's billions USD | [BEA reuse](https://www.bea.gov/help/faq/147) |
| ESTR | ECB EST/B.EU000A2X2A25.WT, percent, effective dates unchanged | [ECB disclaimer](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) |
| ECB_DFR | ECB FM/D.U2.EUR.4F.KR.DFR.LEV; percent per annum, effective dates unchanged | Same ECB terms |
| ECB_ASSETS | ECB ILM/W.U2.C.T000000.Z5.Z01; Eurosystem changing composition, millions EUR; ISO week labelled Friday | Same ECB terms |
| CUSHING_STOCKS, CRUDE_STOCKS_EX_SPR | EIA W_EPC0_SAX_YCUOK_MBBL and WCESTUS1; weekly ending stocks excluding SPR, thousand barrels | [EIA reuse](https://www.eia.gov/about/copyrights_reuse.php) |
| SOFR, EFFR | NY Fed Markets API reference rates; effectiveDate and percentRate unchanged | [NY Fed Terms of Use](https://www.newyorkfed.org/privacy/termsofuse), with the reference-rate and DTCC Solutions notices |
| SRF_CEILING | Upper bound of the FOMC federal funds target range (FRED DFEDTARU), read as `targetRateTo` from the same NY Fed EFFR rows; business days; every row must state both bounds, 0 to 100 bp apart | Same NY Fed terms |
| RRPONTSYD, RRP_LONG | Daily sum of NY Fed overnight fixed-rate Treasury reverse repo accepted amounts, USD / 1e9; term operations excluded; history from 2013-09-23 | Same NY Fed terms |
| SONIA | Bank of England Database IUDSOIA, the delayed Database copy | [Bank of England legal](https://www.bankofengland.co.uk/legal), OGL v3.0 with the SONIA attribution, owner decision `seiche.source-use.2026-11-08.boe-sonia` |
| BOJ_ASSETS | Bank of Japan statistics API, BS01 MABJMTA, total assets, 100 million yen; survey month labelled month-start | [BoJ notice](https://www.stat-search.boj.or.jp/info/notice_en.html), free public site only, owner decision `seiche.source-use.2026-11-08.boj-total-assets` |

ECB exports carry the free-source and modification notices. NY Fed exports
carry the Terms of Use notices. BLS exports carry the statement the API Terms
of Service require: "BLS.gov cannot vouch for the data or analyses derived
from these data after the data have been retrieved from BLS.gov." These
permissions do not qualify unrelated third-party ECB data or EIA commodity
vendor prices. The BEA workbook parser pins the exact worksheet, line, units
and series code; the free API's registration requirement does not apply to
this published XLSX. BLS uses ten-year batches and a daily cache, within the
keyless API limits. The actual October 2025 CPI missing observations carry the
publisher's X footnote about the lapse in appropriations. JSON retains explicit
nulls and CSV empty values; neither exporter imputes them. Native Fed missing
statuses are also preserved.

Owner decisions gate four providers (see
`docs/rights/owner-source-decisions-20261108.md` and
`seiche/source_decisions.json`): BLS, the Bank of England, the Bank of Japan
and, in `fed_releases.py`, the Board's commercial paper release. A capture
still decodes without its decision, but its redistribution status is then
`owner_decision_required` and nothing that reads it is public.

The NY Fed ON RRP query asks for `operationTypes=reverserepo`, fixed-rate
Treasury operations. The API rejects a term filter (HTTP 422, checked
2026-11-09), so term operations in the response are excluded by the parser,
never summed.

All captures are the current publisher vintage. `knowledge_time` is acquisition,
not historical availability. Actual publication time is unknown. Where BEA,
EIA or the Bank of Japan supplies a publication or last-update date it is
separately retained, without inventing a time. Source observation/reference
dates remain separate from both clocks. Per-observation evidence maps
(operation ids, revision clocks) stay in the capture and the single-series
route; catalogs and the board carry their size only.
