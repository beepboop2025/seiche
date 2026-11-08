# Owner source-use decisions: 2026-11-08

On 8 November 2026, at about 22:40Z, the owner (Mrinal) answered the four
open source-use questions in the FRED withheld-data audit with these words:

> "go with them all just make sure everything starts working"

"Them all" are the four decisions below, as they were put to her. Each is
recorded in the machine-readable registry `backend/seiche/source_decisions.json`,
which the adapters and the public rights projection read. A series that needs
one of these decisions is public only while its entry is present there with
`redistribution_status` `allowed`; removing the entry closes it again. This is
an availability policy set by the owner, not a claim that any publisher has
granted Seiche a licence beyond its published terms.

## 1. Federal Reserve commercial paper rates: DTCC origin accepted

- Decision id: `seiche.source-use.2026-11-08.fed-cp`
- Decision: Fed CP rates may be served. The DTCC origin of the Board's
  computation is accepted.
- Question: the Board states that "The Federal Reserve Board's CP release is
  derived from data supplied by The Depository Trust & Clearing Corporation
  (DTCC)", while its notice says Board information is public domain "Unless
  otherwise indicated".
- Series: `CP_NONFIN_3M` (FRED DCPN3M) and `CP_FIN_3M` (FRED DCPF3M), read only
  from the Board's CP release XML zip,
  https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip.
- Adapter status: `fed_cp` captures carry `redistribution_status` `allowed`
  and the decision summary; the attribution names the Board's DTCC derivation.
- Conditions: serve only the Board-published 90-day AA rates, never the
  underlying DTCC trade records; cite the Board.
- Terms: https://www.federalreserve.gov/disclaimer.htm and
  https://www.federalreserve.gov/releases/cp/about.htm

## 2. BLS series through the BLS API: Terms of Service accepted

- Decision id: `seiche.source-use.2026-11-08.bls-api`
- Decision: BLS series are read through the BLS API and its Terms of Service
  are accepted.
- Series: `CORE_CPI` (CUSR0000SA0L1E) and `ENERGY_CPI` (CUSR0000SA0E), from the
  keyless v1 Public Data API with no registration key.
- Adapter status: `bls_original` captures carry `allowed`, the decision
  summary, and the statement the Terms of Service require on every served
  copy: "BLS.gov cannot vouch for the data or analyses derived from these data
  after the data have been retrieved from BLS.gov."
- Terms: https://www.bls.gov/developers/termsOfService.htm and
  https://www.bls.gov/opub/copyright-information.htm

## 3. Bank of Japan total assets on the free, no-paywall site

- Decision id: `seiche.source-use.2026-11-08.boj-total-assets`
- Decision: Bank of Japan total assets may be served on the free, no-paywall
  site.
- Question: the Bank of Japan permits copying with explicit credit, except
  "The copying or reproduction of the content for commercial purposes."
- Series: `BOJ_ASSETS` (FRED JPNASSETS), now read from the keyless BoJ
  statistics API, database BS01, series MABJMTA ("Bank of Japan Accounts/Assets/Total"),
  100 million yen, monthly.
- Adapter status: `boj_original` captures carry `allowed`, the Bank of Japan
  credit and the free-site condition. The rights projection allows this
  source only on free surfaces; a paid or metered surface cannot read it.
- Terms: https://www.stat-search.boj.or.jp/info/notice_en.html

## 4. SONIA served raw under the Bank of England's open licence

- Decision id: `seiche.source-use.2026-11-08.boe-sonia`
- Decision: SONIA is served raw under the Bank of England's open licence, with
  its attribution.
- Series: `SONIA` (IUDSOIA) on the legacy board, from the Bank of England
  Database, and the atlas instrument `GB.BOE.SONIA`.
- Adapter status: `boe_original` captures carry `allowed`; the UK pack's
  `boe_sonia` adapter is official-open and `allowed` while the decision is
  recorded, with the Database copy's clock (by 10:00 London on the business
  day after first publication). Every served value carries: "SONIA and/or
  SONIA Compounded Index data licensed under the Open Government Licence v3.0
  and copyright the Governor and Company of the Bank of England."
- Terms: https://www.bankofengland.co.uk/legal and
  https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/

## What these decisions do not change

They do not qualify any FRED copy, the 17 blocked series (VIX, the ICE BofA
spreads, S&P 500, Nasdaq, the EIA spot prices Refinitiv supplies, the IMF
commodity series, TED and NFCI), the OECD call rates fetched from FRED, or any
source whose reuse terms are not established. Readings that read any of those
stay withheld under policy `seiche.public-source-rights.2026-11-09`; see
`public-legacy-projection-20261109.md`.
