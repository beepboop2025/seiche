# Bank of England Database XML exports

Unmodified XML responses retrieved on 2026-11-02 from
`https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp`
with `CodeVer=new`, `xml.x=yes`, `Datefrom=01/Jul/2025`, `Dateto=now`,
`UsingCodes=Y`, `VPD=Y`, `VFD=N` and one `SeriesCodes` value per file.

| File | Series | SHA-256 |
| --- | --- | --- |
| `YWMB83C.xml` | Operational Standing Facility rate, deposit, by maintenance period | `0798d737085a6235abef7189c0085587bfba04fbbea730f93de4168e608bb6fe` |
| `YWMB82C.xml` | Operational Standing Facility rate, lending, by maintenance period | `3a5519909bf13db5f0d1106d2e8f222d1a6c5c6b7df3d0f2e4a98f043ad819d9` |
| `LPMBL22.xml` | Monthly average of Wednesday sterling reserve balances (GBP millions) | `6ac695682f8f410e5533fd69ba79b3518038fa6be9d1cdfbeddd01d3bd9279f9` |
| `YWMB82C-empty-window.xml` | YWMB82C requested with `Datefrom=01/Oct/2026`: series metadata and no observation | `627526a67e8cad245edc124f9fb095525cd7b6bf559d67c3e02c2a761f829077` |

Publisher: Bank of England. Reproduction of Database data is subject to the
UK Open Government Licence v3.0 (https://www.bankofengland.co.uk/legal);
contains Bank of England data, copyright the Governor and Company of the Bank
of England. The XML export, unlike the CSV, carries each observation's
`LAST_UPDATED` time. The fixtures test series identity, units and those
clocks; they are not a current production dataset.
