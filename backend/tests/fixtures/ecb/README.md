# ECB Data API fixture

`ILM-W-total-assets-2026-09.csv` is the ECB Data API response of 2026-11-02 to
`https://data-api.ecb.europa.eu/service/data/ILM/W.U2.C.T000000.Z5.Z01?format=csvdata&startPeriod=2026-09-01`
(Eurosystem total assets, weekly, EUR millions), verbatim. It is used by
`backend/tests/test_ecb_total_assets.py`. The row for 2026-W40 carries no
`UNIT_MULT`, as published.

Source: European Central Bank. Reuse of ECB information requires that it
"must appear accurately and the ECB must be cited as the source"
(`https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html`).
