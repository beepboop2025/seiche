# Federal Reserve Board release package fixtures

Trimmed copies of the data members of the Board's own release packages, used by
`backend/tests/test_frb_release_package.py`, `test_fed_h41.py`,
`test_fed_prates.py` and `test_fed_release_foundation.py`.

Source: Board of Governors of the Federal Reserve System, the release packages
at `https://www.federalreserve.gov/releases/<rel>/data/FRB_<REL>_xml.zip`
(PRATES, H.4.1, H.15, H.10, G.17), downloaded on 2026-11-02, except
`G17_data.xml`, trimmed from the G.17 package the Board republished on
2026-11-24 (its annual revision, downloaded on 2026-11-25). Board information
is public domain unless otherwise indicated, and the Board asks to be cited as
the source (`https://www.federalreserve.gov/disclaimer.htm`).

Each file keeps the published SDMX-ML structure: the message namespaces, the
`DataSet` element with its `id` and series namespace, every series' attributes
in published order, and the observation rows verbatim for a short window:

| File | Data set | Window | Series |
|---|---|---|---|
| `H41_data.xml` | `H41` | 2026-10-07 to 2026-10-28 | the nine pinned series and six neighbouring decoys |
| `H15_data.xml` | `H15` | 2026-10-23 to 2026-10-29 | the six pinned yields and bill rates, the two held commercial paper series and four decoys |
| `H10_data.xml` | `H10` | 2026-10-19 to 2026-10-23 | the 25 pinned rates and indexes and one decoy |
| `G17_data.xml` | `IP_MAJOR_INDUSTRY_GROUPS` and two others | 2026-06-30 to 2026-10-31 | `IP.B50001.S` in all three data sets that carry it, and the not seasonally adjusted decoy, both declaring `UNIT="Index:_2022_100"` |
| `G17_data_2017_base.xml` | `IP_MAJOR_INDUSTRY_GROUPS` and two others | 2026-06-30 to 2026-09-30 | the same trim of the 2026-11-02 package, the last before the annual revision, declaring `UNIT="Index:_2017_100"`; kept to prove the retired base fails closed |
| `PRATES_data.xml` | `PRATES_POLICY_RATES` | IOER and IORR 2021-07-24 to 2021-07-28; IORB 2021-07-29, 2021-07-30 and 2026-10-28 to 2026-11-02 | IOER, IORR (decoy) and IORB |

The G.17 annual revision of 2026-11-24 changed only the `UNIT` declaration of
these series (`Index:_2017_100` to `Index:_2022_100`) and restated every level,
including the months both files share: the history was revised, not only
rescaled.

The three commercial paper series in `H15_data.xml` (`RIFSPPNAAD90_N.B`,
`RIFSPPFAAD90_N.B` and the `RIFSPPNAAD60_N.B` decoy) carry **synthetic values**
(1.11 to 3.34) and synthetic availability flags, not the published rates: the
Board derives those rates from DTCC data and their redistribution awaits an
owner rights decision. Their series attributes and dates are as published.
