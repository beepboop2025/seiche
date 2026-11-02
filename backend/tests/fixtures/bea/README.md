# BEA NIPA flat-file fixtures

Trimmed copies of two files BEA posts at
`https://apps.bea.gov/national/Release/TXT/`, downloaded on 2026-11-02 and
used by `backend/tests/test_bea_nipa.py`:

- `NipaDataQ.txt`: the header and, verbatim, the first and the nine latest
  `A191RC` quarters (nominal GDP, USD millions at a seasonally adjusted annual
  rate) plus a few neighbouring `A191RX` and `A191RL` rows that must never be
  read as GDP.
- `SeriesRegister.txt`: the header and the register rows for `A001RC`,
  `A191RC`, `A191RL` and `A191RX`, verbatim.

Source: U.S. Bureau of Economic Analysis. BEA web information is public domain
unless stated otherwise (`https://www.bea.gov/help/faq/147`).
