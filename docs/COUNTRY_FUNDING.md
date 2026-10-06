# Country funding and sovereign references

The country desk adds 34 profiles to the existing USD and India desks. It reads
completed canonical observations; opening the API, MCP tool or dashboard never
starts a source download. Collection remains in the scheduled official worker.
Code, collection qualification, deployment and public release are separate gates.

## Coverage

| Countries | New official public observations | Main remaining limitation |
| --- | --- | --- |
| China | BIS policy reference; OECD/FRED monthly three-month interbank reference | No admitted sovereign curve; CFETS/SHIBOR entitlements are unchanged |
| Japan | MOF constant-maturity JGB yields at 1–10, 15, 20, 25, 30 and 40 years | Source-calculated maturity references, not individual traded bonds |
| South Korea | BIS policy reference; OECD/FRED monthly three-month interbank and ten-year government-bond references | Monthly observations do not supply a daily curve; BOK ECOS access rules remain separate |
| Taiwan | CBC dated discount decision and overnight call rate; eight monthly selected-rate columns, including a ten-year government-bond reference | Monthly averages and month-end bank/policy rates have different definitions |
| Australia | OECD/FRED monthly ten-year government-bond reference | RBA cash/policy adapters remain separate; no newly qualified daily government curve |
| United Kingdom | BoE nominal zero-coupon spot yields at 5, 10 and 20 years | Spot yields are not par yields; existing SONIA ingestion policy remains separately gated |
| Germany, France, Spain, Italy, Netherlands, Belgium, Austria, Portugal, Ireland, Finland, Greece, Slovakia, Slovenia, Lithuania, Latvia, Estonia, Croatia, Cyprus, Malta, Luxembourg, Bulgaria | ECB monthly national long-term interest-rate references | One approximate ten-year monthly reference per issuer; source-defined proxies are retained |
| Switzerland, Sweden, Norway, Denmark, Poland, Czechia, Hungary | BIS policy references; OECD/FRED monthly ten-year government-bond references | Local overnight, repo, facility and reserve coverage is incomplete |

Existing admitted policy, funding and liquidity observations are included from
the relevant market pack. Euro-area countries share the `EA-EUR` monetary-policy,
overnight and liquidity references, while their sovereign series remain in
separate national packs. National issuers are not counted as additional clearing
systems. Registering a reference pack does not establish calibrated engine support.
The new reference-only packs seal overview and gauge records with a null,
unavailable gauge and false evidence eligibility. Their missing calendar evidence
remains a validation failure; successful collection cannot promote it to a pass.

The isolated 2026-10-06 qualification captured all 44 new adapters successfully
and produced at least one dated observation for every profile. It does not prove
complete coverage, production activation or historical point-in-time availability.

## Read the data

```text
GET /api/v2/country-funding
GET /api/v2/country-funding/JP
GET /api/v2/country-funding/FR
```

The first route is a structural catalog. A country route returns
`seiche.country-funding-curve.v1`, including policy, money-market and liquidity
sections, admitted sovereign curves, coverage gaps and source links. Country
codes are ISO alpha-2; `UK` aliases `GB`. Query parameters and unknown country
codes are rejected. Reads are bounded to the requested country, its monetary
area, and Germany when needed for a monthly euro-area spread. Default history
is 400 days from the start of the boundary date; Taiwan's dated discount decision
has a separate bounded window. Website and API projections use the same bounds.

The existing `money_market_context` MCP tool exposes the same chartless payload:

```json
{"section":"countries","country":"JP"}
```

Omit `country` to list profiles. `country` is accepted only with
`section="countries"`. The hosted tool count remains sixteen. The Money Markets
tab offers a country selector, source-linked tables and dated curve points from
the same projection embedded in the completed global atlas.

## Dates, units and interpretation

- Rates enter the canonical store as basis points and are displayed as percent.
  Slopes and changes remain in basis points. Conventions are preserved per source.
- Daily source dates, monthly observation periods, effective policy-decision
  dates, publication time and first capture are distinct. No download timestamp
  becomes a source observation or historical knowledge clock.
- Monthly periods use a calendar month-end storage label and expose
  `observation_period`. This is a period label, not a month-end market quote.
- The new files do not supply a reliable publication timestamp for every row.
  Those timestamps stay null, and publication freshness stays unknown. A source's
  scheduled release time is not substituted for an actual publication receipt.
- A monthly CBC discount or bank rate is an end-of-period reference; overnight
  call, commercial-paper and government-bond rates are source-defined monthly
  market averages. The current CBC discount indicator keeps its decision date.
- Curve changes require matching latest and previous observation dates within
  one curve family. Missing maturities are not interpolated. Unknown freshness
  yields historical-reference movement, not a claim about today's market.
- The spread to Germany uses only matching ECB national monthly observations.
  It is not a live tradable bond spread or a country-risk score.
- BIS rows explicitly marked missing are gaps. An unflagged non-finite rate,
  conflicting missing flag, changed unit, wrong country or non-public observation
  fails parsing. Missing observations are never zero-filled or forward-filled.
- A historical file captured now establishes today's knowledge of that history.
  It does not establish what Seiche knew at the historical observation time.

## Sources and reuse review

Review date: 2026-10-06. These grants apply to the named datasets; they do not
change another publisher's rights or retroactively relabel stored restricted rows.

| Source | Dataset and method | Reuse basis |
| --- | --- | --- |
| Japan Ministry of Finance | [JGB interest-rate files](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/index.htm); [calculation method](https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm) | [Public Data License 1.0](https://www.mof.go.jp/english/about_mof/notice/index.html); attribute MOF and identify transformations |
| Bank of England | Database series `IUDSNZC`, `IUDMNZC`, `IUDLNZC`; [yield-curve methodology](https://www.bankofengland.co.uk/statistics/yield-curves) | [Database Open Government Licence terms](https://www.bankofengland.co.uk/legal); these three yield series are distinct from excluded third-party exchange rates |
| ECB and national contributors | [IRS long-term interest-rate references](https://data.ecb.europa.eu/data/concepts/long-term-interest-rates), scoped national `M.<country>.L.L40.CI.0000.EUR.N.Z` series | [ECB reuse terms](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html); attribution, accurate transformation labels and notice that originals are free from ECB |
| BIS and reporting central banks | [Central bank policy rates](https://data.bis.org/topics/CBPOL); [source and methodology](https://www.bis.org/statistics/cbpol/cbpol_doc.pdf) | [BIS legal terms](https://data.bis.org/help/legal); name BIS and the reporting central bank; no separate charge for BIS statistics or implied endorsement |
| OECD via Federal Reserve Bank of St. Louis FRED | Main Economic Indicators monthly ten-year government yields and China/Korea three-month interbank references; exact series URL supplied with each metric | [OECD data terms, section 3](https://www.oecd.org/en/about/terms-conditions.html), with each FRED series' OECD source and citation-required metadata checked; retain attribution and access date |
| CBC Taiwan | [Dated indicators](https://www.cbc.gov.tw/en/mp-2.html), [Selected Interest Rates](https://www.cbc.gov.tw/en/cp-511-1876-0EF53-2.html), [definitions](https://www.cbc.gov.tw/en/cp-515-29985-C1D35-2.html) | [Open Government Data License Taiwan 1.0](https://www.cbc.gov.tw/en/cp-958-40419-F8209-2.html); attribute CBC and identify transformations |

Each metric includes a source URL and its relevant publisher/terms. Original
captures, hashes, canonical row evidence and capture clocks are retained by the
existing collector pipeline. Source files are size-bounded and requests use
fixed official hosts and series identifiers. No paid credentials or scraping
circumvention is part of this expansion.

The existing CFETS, licensed market and BOK access policies remain intact.
The BoE's current legal page expressly permits SONIA reuse under OGL, but the
new qualification did not establish a working public SONIA fetch; no existing
restricted observation is promoted by that rights review alone. Source access,
rights review and canonical publication are separate requirements.

## Qualification and release

Tests exercise country identity, rights, temporal bounds, missing-value semantics,
month labels, source-schema changes, mixed-date rejection, precision and REST/MCP
parity. Original public-file canaries additionally validate actual remote schemas.
Full application qualification, source review, recovery, deployment, scheduled
collection, served API/MCP/dashboard parity and immutable distribution receipts
remain required before this candidate can be called released.
