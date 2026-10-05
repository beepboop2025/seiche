# India funding and sovereign curves

The India view in Money Markets combines dated RBI funding observations with
separate daily benchmark, monthly maturity and primary-auction references.
`GET /api/v2/india-funding` and `money_market_context({"section":"india"})`
return the same chartless `seiche.india-funding-curve.v1` payload.
The Global Money Market Atlas embeds the desk at
`markets[market_id=IN-INR].funding_curve`, including bounded chart histories.
All readers use canonical observations and never start collection.

## Source contracts

| Adapter | Official source and scope | Native cadence / polling |
| --- | --- | --- |
| `rbi_official` | [RBI release feed](https://www.rbi.org.in/pressreleases_rss.xml), with [MMO page](https://www.rbi.org.in/Scripts/BS_ViewMMO.aspx/Statistics.aspx) fallback: CALL WAR, overnight TREPS and market repo WAR, SDF/MSF/VRR/VRRR amounts, net injection, cash balances, reserve requirement and separately dated durable liquidity | Daily reports, including weekend reports / hourly |
| `rbi_rates_weekly` | [Cash reserve ratio and interest rates](https://www.rbi.org.in/Scripts/BS_NSDPDisplay.aspx?param=4): dated repo, CRR and 91/182/364-day primary T-bill yields | Weekly / daily |
| `rbi_policy` | [Monetary policy index](https://www.rbi.org.in/Scripts/annualpolicy.aspx): bounded discovery of four recent full documents, admitting only dated MPC resolutions with repo/SDF/MSF rates | Event-driven; daily polling |
| `rbi_liquidity_weekly` | RBI Weekly Statistical Supplement: “Liquidity Operations by RBI”, including OMO sales/purchases and facility amounts | Weekly / daily |
| `rbi_credit_fortnightly` | RBI Weekly Statistical Supplement: “Certificates of Deposit” and “Commercial Paper”; issuance/outstanding and reported rate ranges | Fortnightly / daily |
| `rbi_sovereign` | [RBI homepage](https://www.rbi.org.in/), Government Securities Market section only: named approximately 3/5/10/15/30Y bonds | Daily / daily |
| `rbi_monthly_curve` | RBI DBIE report 217, “Month-end Yield of SGL Transactions in Government Dated Securities for Various Maturities”, from the [RBI Innovation Hub archive](https://github.com/Reserve-Bank-Innovation-Hub/dbie.rbihub.in) | Calendar month / weekly |
| `rbi_auctions` | RBI feed full government-stock auction results, plus the dated [4 September 2026 reference](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63517) containing the 40Y bond | Irregular auction events; weekly reference cadence / daily polling |

WSS discovery checks at most three Friday editions of
`WSSViewDetail.aspx?PARAM1=M%2FD%2FYYYY&TYPE=Basic`, following only recognized
RBI `WSSView.aspx?Id=` table links. Missing editions/rows remain unavailable.
The monthly connector follows the public archive's `latest.json` and manifest
at `https://dbie-common-scrapes.s3.ap-south-1.amazonaws.com/scrapes`, verifies
the exact report path, byte length and SHA-256, and checks the 1–30Y column
labels. The unlabelled SDMX export is unsuitable for this series and is not
used. The pack's reviewed calendar begins in 2001; older rows are not imported.

## Meaning and clocks

- Rates normalize from percent to basis points. Money normalizes from INR crore
  to INR millions; the India desk displays crore with matching changes/history.
- The homepage's FX clock cannot date policy rates or T-bills. Only the bond
  section's own observation date is used. Undated legacy homepage rate series
  receive no new observations.
- MMO totals sum every maturity under a facility. Net injection remains
  positive for injection and negative for absorption. Durable liquidity has
  its own observation date and its source's surplus-positive convention.
  It is not added to the net-injection ledger. Missing amounts are not zero.
- RBI RSS publication times lacking a timezone are interpreted in the pack's
  Asia/Kolkata timezone with **estimated** quality. Date-only release/weekly/
  monthly clocks remain explicitly unknown. An unknown publication instant is
  not replaced by the collection time or silently labelled fresh.
- Knowledge time is the actual Seiche capture. Repeated identical rows retain
  their first-seen knowledge time; revisions remain append-only. A current
  historical file does not reconstruct historical information vintages.
- Bond yield day-count/compounding is `source_native`; the collector does not
  assert overnight simple-rate conventions for a sovereign YTM.
- CD/CP ranges describe issuance during a fortnight. They are neither a
  standardized 3M benchmark nor executable quotes. T-bill references are
  primary-auction observations, not daily secondary-market prices.

## Curves and mechanism

The requested 1/2/3/5/7/10/14/30/40Y slots remain visible. The monthly report
provides the first eight through 30Y. A dated 40Y **auction** reference appears
under bond auctions; it never fills a daily or monthly secondary-market node.
The homepage's 15Y benchmark is not relabelled 14Y. No interpolation,
extrapolation, forward filling or cross-source tenor splicing is performed.

`2s5s`, `2s10s`, `5s10s`, `10s30s` are long minus short yield in basis points.
Each curve family uses its own exact common dates. Both legs must have the
same latest date. Daily movement requires current observations and the same
named securities across dates. Benchmark changes reset own-history statistics
and withhold the cross-roll movement. Monthly movement is always historical
context with the compared month ends attached, even when its publication
instant is unknown; it never sets today's headline.

Positive changes in both yields are bear moves; negative changes in both are
bull moves. A positive spread change is steepening and a negative one is
flattening. Changes within 0.1bp are treated as flat. Parallel, one-leg and
opposite-direction moves retain their own labels instead of being forced into
one of the four conventional quadrants.

A bear-flattening mechanism is only “consistent with” withdrawal when RBI net
absorption increases over the exact same two dates. A newer liquidity report
cannot substitute for one of those dates. Evidence hashes accompany the
matched window. Bond supply, policy/inflation expectations and duration demand
remain alternative explanations. No causal identification, forecast, alert of
impending stress, or trade recommendation is claimed.

## Access policy and alternatives investigated (5 October 2026)

These connectors extend the pack's existing RBI public-observation policy.
They attribute source facts and retain raw captures for operator verification.
Neither RBI's website nor the Innovation Hub software licence is represented
as a blanket commercial data licence. See [RBI's disclaimer](https://www.rbi.org.in/Scripts/Disclaimer.aspx).
Redistribution is checked both against the adapter and against each row.
CCIL/FBIL and tenant/private inputs remain separate and are not acquired by
these public collectors. The user confirmed no CCIL/FBIL entitlement.

| Alternative | Verified outcome |
| --- | --- |
| RBI DBIE / Innovation Hub original report | Labelled monthly 1–30Y observations admitted; archive hash checked. Daily full curve is not supplied. |
| RBI weekly releases and auction results | Provide OMO, CD/CP ranges, bills and a 40Y primary-auction reference without pretending these are daily benchmark quotes. |
| [CCIL tenorwise indicative yields](https://www.ccilindia.com/web/ccil/tenorwise-indicative-yields), zero rates and ZCYC parameters | Useful full-curve candidates, but explicit written-permission requirement for commercial use; no entitlement, no collector activation. |
| [Capera curve](https://capera.co/data/India/yield-curve) | Public 3M–40Y display, but upstream provenance only “published G-Sec yields” and [terms](https://capera.co/terms) do not establish redistribution permission. Not admitted. |
| [Bonds API](https://bonds-api.com/) | Alternative API offering. [Terms](https://bonds-api.com/terms) permit public/commercial responses only under a paid subscription; a trial is personal/testing only. No subscription purchased or trial started. |
| NSE reports / Market Pulse | Exchange data have separate access/use terms; reports cite external data vendors. No independent daily redistribution entitlement established. |
| [FRG historical curves](https://ifrogs.org/dms/zcyc/zcyc_webpage.html) | Historical modelled curves ending in 2020; not a current daily feed. |
| ADB AsianBondsOnline, data.gov.in, international long-rate series and retail/aggregator pages | No verified current India 1–40Y daily dataset with the required identities and reusable provenance found. |

## Operation and remaining boundaries

The eight adapters join the existing independent collector schedules; no new
daemon, paid service, secret or production writer is introduced. After the
normal backend release, an authorized operator can initialize the pack with
`seiche market-collect --market IN-INR --no-materialize` against the intended
runtime store. Scheduled collection then accumulates daily history. Collector
failures remain per-source and cannot erase successful sibling observations.

`IN-INR` remains **reference context**, with `historically_validated=false` and
`coverage.complete=false`. Complete daily 1–40Y coverage, standardized CD/CP
term benchmarks, validated past-information claims and observed production
recurrence remain separate from this implementation. A frontend-only release
cannot activate these backend collectors. Use the normal signed application
release and verify served website/API/MCP data after deployment.

Deterministic coverage is in `backend/tests/test_india_funding.py`, alongside
existing India freshness, official-adapter, atlas, API and MCP tests. Live
captures and databases belong in external verification storage, not Git.
