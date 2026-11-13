# Rates source fixtures

Verbatim publisher responses, kept small, used by the offline rates tests
(`backend/tests/test_rates_sources.py`, `backend/tests/test_rates_desk.py`). Tests never touch the network.

## ECB yield curves (YC dataset)

Retrieved 2026-11-13 (19:12Z) from the ECB Data API, `format=csvdata`, `startPeriod=2026-11-09`,
`endPeriod=2026-11-12`:

| File | Request |
| --- | --- |
| `ecb-yc-aaa-sr10y-2026-11-09-to-12.csv` | `https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y` |
| `ecb-yc-aaa-sr2y-2026-11-09-to-12.csv` | `https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y` |
| `ecb-yc-aaa-sr3m-2026-11-09-to-12.csv` | `https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3M` |
| `ecb-yc-all-sr10y-2026-11-09-to-12.csv` | `https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_C.SV_C_YM.SR_10Y` |

`G_N_A` is the triple A sample, `G_N_C` all euro area central government bonds. Spot rates, Svensson model,
continuous compounding, percent per annum (`UNIT=PCPA`, `UNIT_MULT=0`).

Source: European Central Bank. Reuse of ECB information requires that it "must appear accurately and the ECB
must be cited as the source" (`https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html`,
read 2026-11-13).

## ECB euro short-term rate distribution and compounded series (EST dataset)

Retrieved 2026-11-13T20:00:01Z from the ECB Data API, `format=csvdata`, `startPeriod=2026-11-09`,
`endPeriod=2026-11-13`, request `https://data-api.ecb.europa.eu/service/data/EST/B.<series>`:

| File | Series | Meaning |
| --- | --- | --- |
| `ecb-est-eu000a2x2a25-r25-2026-11-09-to-13.csv` | `EU000A2X2A25.R25` | rate at the 25th percentile of volume, percent |
| `ecb-est-eu000a2x2a25-r75-2026-11-09-to-13.csv` | `EU000A2X2A25.R75` | rate at the 75th percentile of volume, percent |
| `ecb-est-eu000a2x2a25-nt-2026-11-09-to-13.csv` | `EU000A2X2A25.NT` | number of transactions |
| `ecb-est-eu000a2x2a25-nb-2026-11-09-to-13.csv` | `EU000A2X2A25.NB` | number of active banks |
| `ecb-est-eu000a2x2a25-vl-2026-11-09-to-13.csv` | `EU000A2X2A25.VL` | share of volume of the five largest active banks, percent |
| `ecb-est-eu000a2qqf24-cr-2026-11-09-to-13.csv` | `EU000A2QQF24.CR` | compounded average rate, one month tenor, percent |
| `ecb-est-eu000a2qqf08-ci-2026-11-09-to-13.csv` | `EU000A2QQF08.CI` | compounded index, 1 October 2019 = 100 |

The distribution series are dated with the trading day (published the next TARGET day at 08:00 Frankfurt time); the
compounded rate and index are dated with their publication day. Source: European Central Bank (terms as above).

## Bank of Canada Valet (14 November 2026)

`boc-valet-2026-11-09-to-13.json` is the original Valet JSON response for the 16
series declared in `markets/canada_cad.py`, requested with start_date=2026-11-09
and end_date=2026-11-13. Publisher: Bank of Canada. Terms:
https://www.bankofcanada.ca/terms/ (read 2026-11-14). The original data are freely
available from the Bank of Canada. Fixture bytes are retained; parser unit and
identity mutations occur only in memory in tests. November 11 CORRA is missing
for the bank holiday, while any policy observations actually printed on that
date retain their source date. No dates are filled.
