# Seiche data coverage ledger

Status date: 2026-08-22

This is the durable engine-to-data coverage record for Seiche. It answers three
questions:

1. Which real inputs does every published engine consume?
2. Which engines are already source-complete, partial, or deliberately null?
3. Which data work changes a user-visible result instead of merely adding rows?

The machine-readable source-candidate and backlog record is
[`data-source-expansion.json`](data-source-expansion.json). Keep source rights,
access, and implementation state there; this document explains their product
impact.

## Decision summary

- The legacy board is already broad and numerically functional. A dated local
  last-known-good snapshot had all 40 then-existing light blocks `ok=true`; the
  subsequently added `money_market` desk is source-complete and designed to keep
  partial metrics visible.
- The dated deep cache had every analytic block `ok=true` except `backtest`.
  That failure is intentional: final/current-vintage history is not signed
  as-published evidence.
- The highest-value nulls are Global Tide, validated Backtest, China's public
  local gauge, and New Zealand's required corridor input. Korea now has a
  forward calibration over its two implemented BOK series; its optional
  corridor remains unavailable until official facility-rate rows exist.
- US, euro-area, UK, Japan, Hong Kong, India, Australia, Korea, and Singapore
  already have a real implemented path for each required local-gauge component.
  Their next step is durable canonical history, scheduling, materialization, and
  validation—not duplicate vendor acquisition.
- Navigator's dated failure was an unconfigured model endpoint. Market-data
  acquisition will not fix it.

## What the counts prove—and do not prove

The audit used a read-only local development SQLite cache as a dated coverage
sample. It is **not** a query of the Hetzner production database and must not be
presented as current production health.

| Measure | Dated local result |
|---|---:|
| Registered legacy series | 115 |
| Series present in the cache | 105 |
| Stored legacy observations | 180,036 |
| Latest observation date | 2026-08-20 |
| Fresh under native-cadence rules on 2026-08-22 | 69 |
| Aging | 34 |
| Dead by design | 2 |
| Registered but absent from the dated cache | 10 |

The two dead rows are intentional:

- `IOER` ended in 2021 and is spliced into `IORB` history.
- `TED` ended in 2022 and is historical ML pretraining only.

The ten locally absent rows—`NZD`, `DKK`, `HKD`, `MYR`, `NOK`, `SEK`, `SGD`,
`TWD`, `THB`, and `LKR`—are already registered FRED H.10 series. They need a
normal backfill/sweep, not source research.

### Post-audit real sweep

The new source-only collector was then run from the clean release worktree on
2026-08-22. It completed in 45 seconds with 29 of 29 source groups successful,
zero degraded, zero failed, and zero faults. The local cache grew from the
dated audit's 180,036 observations to 204,980. All ten H.10 currencies above
were filled with 2,402 observations each (24,020 total), covering 2017-01-03
through 2026-08-14. A subsequent complete board build returned 100% coverage.

This is local functional evidence, not a production-health claim. The same
series counts, worker heartbeat, and board result must be independently checked
on Hetzner after release.

Structured NY Fed, Treasury, and CFTC table envelopes currently carry a fetch
clock but report observation freshness as `unknown`. A successful HTTP fetch is
not proof that every row in a heterogeneous table is current. See
`backend/seiche/assemble.py:1589-1631`.

## Status vocabulary

| Status | Meaning |
|---|---|
| `READY` | Dated evidence and code show enough real input for a numeric result. |
| `READY_PARTIAL` | Numeric output works, but a named subscore/history/channel is still accruing or optional data is absent. |
| `LIVE_ONLY` | Works for the current board but cannot be honestly reconstructed for past dates. |
| `FORWARD_ONLY` | Computes now, but its validation record is still accruing. |
| `UNAVAILABLE` | A required input, right, evidence cut, or service configuration is missing. |
| `ALIAS` | Published compatibility key; no independent engine or source. |

“Implemented official adapter” means production collector code exists. It does
not by itself prove the most recent Hetzner run succeeded.

## Shared legacy inputs

- `SPREAD` = `(SOFR - splice(IORB, IOER)) * 100`, in basis points.
- `TAIL` = NY Fed SOFR `percentPercentile99 - percentRate`, in basis points.
- `CP_SPREAD` = `(CP_NONFIN_3M - DGS3M) * 100`, in basis points.
- `RES_GDP` = `WRESBAL / GDP` after unit normalization.
- `SRF` = accepted Standing Repo Facility amount.
- `DW` = `DISCOUNT_WINDOW`; `RRP` = `RRPONTSYD`; `TGA` = Treasury daily cash.

These derivations are centralized at `backend/seiche/assemble.py:358-421`.

## Legacy light-board engine matrix

Every block below is returned under `payload.engines`, included in
`/api/overview`, and addressable through `/api/engines/{name}`.

| Engine | Status | Exact input contract | Remaining data issue |
|---|---|---|---|
| `money_market` | `READY_PARTIAL` | `SOFR`, `EFFR`, `IORB/IOER`; NY Fed SOFR/TGCR/BGCR frames; OFR `BGCR`, `TGCR`, DVP/TRI/GCF rates and volumes, MMF totals/repo; `CP_NONFIN_3M`, `CP_FIN_3M`, `DGS3M`, `TB4W`, `TB3M`, `WRESBAL`, `TGA`, `RRP`, `SRF`, `DW` | Individual cards fail closed when a leg is absent; SEC N-MFP would add holdings/counterparty depth. |
| `kink` | `READY` | `SPREAD`, `WRESBAL`, `GDP` | None; IOER discontinuation is handled by the splice. |
| `rdenowcast` | `READY` | Kink result, NY Fed RDE, `SPREAD`, `WRESBAL`, `GDP` | None. |
| `weather` | `READY` | `WRESBAL`, `WALCL`, `TGA`, kink, upcoming auction settlements | None. |
| `supplydesk` | `LIVE_ONLY` | Upcoming auctions, historical auctions, MSPD maturities | Historical current-state auction vintages do not exist. |
| `runway` | `READY` | `WRESBAL`, `RRP`, `TGA`, kink, settlements, configured QT pace | QT pace remains a declared assumption. |
| `tails` | `READY` | NY Fed secured-rate distributions, spliced IORB | None. |
| `stigma` | `READY` | NY Fed SOFR frame, `SRF_CEILING`, `SRF`, IORB | None. |
| `echo` | `READY` | `SPREAD`, `EFFR-IORB`, `BGCR-SOFR`, `RRP`, TGA 5-day change, reserves 4-week change, `SRF` | None. |
| `rvxray` | `READY` | CFTC Treasury TFF, OFR `DVP_VOL` | None. |
| `crowding` | `READY` | CFTC Treasury TFF | Shares the RV X-Ray module; no separate source. |
| `officialbid` | `READY` | `CUSTODY_TSY`, `FOREIGN_RRP`, `FIMA_REPO` | Treasury TIC would add holder/country depth. |
| `auctions` | `READY` | Treasury auction table | None. |
| `reportcard` | `READY` | Auction results/index, `SPREAD`, `SRF`, `WRESBAL` | None. |
| `ledger` | `READY` | `WALCL`, `WCURCIR`, `WRESBAL`, `WTREGEN`, `RRP`, `FOREIGN_RRP` | None. |
| `resonance` | `READY` | `SPREAD` | None. |
| `undertow` | `READY` | `SPREAD`, `TAIL` | None. |
| `phasemap` | `READY` | `SPREAD`, Treasury auctions | None. |
| `edetect` | `READY` | `SPREAD`, `TAIL` | None. |
| `communique` | `READY` | Vintage-stamped FOMC statement texts | Continue forward capture. |
| `scuttlebutt` | `READY_PARTIAL` | GDELT WEB-NGRAM topic history | Roughly 60-day bounded history; context-only. |
| `breakwater` | `READY` | `SPREAD`, `SRF` | None. |
| `hydrophone` | `READY` | `SPREAD`, `EFFR-IORB`, `BGCR-SOFR`, `TGCR-SOFR`, DVP-TRI rate, `TAIL`, `SRF`, `RRP`, `TGA`, DVP/TRI volumes | None. |
| `merian` | `READY` | Same plumbing panel as Hydrophone | None. |
| `roguewave` | `READY` | `SPREAD` | None. |
| `caesar` | `READY` | `SPREAD` | None. |
| `warehouse` | `READY` | NY Fed primary-dealer position tables | SEC N-MFP and Treasury TIC would add counterpart/holder structure. |
| `basins` | `READY_PARTIAL` | `SPREAD`, `ESTR`, `ECB_DFR`, `SONIA`, `DXY_BROAD`, `SWAP_LINES`, `FOREIGN_RRP`, NY Fed FX operations, `INR`, USDT peg, `TONA`, `SHIBOR_ON`, `CNY`, `JPY`, `KRW` | China z-score unlocks at 60 observations; dated cache had 58. |
| `thermohaline` | `READY` | BIS `GLI_OFFSHORE_USD/LOANS/DEBT`, `GLI_EME_USD`, `CREDIT_GAP_US/CN` | Quarterly publication lag is native, not failure. |
| `harbors` | `READY_PARTIAL` | Euro `ESTR/EURUSD`; China `SHIBOR_ON/CN_FDR007/CNY`; India `CALL_IN/INR`; Japan `TONA/JPY`; Korea `CALL_KR/KRW`; US `EFFR` | Wire canonical daily RBI/BOK rates into legacy; China percentile history is still accruing. |
| `spillover` | `READY_PARTIAL` | `EFFR`, `ESTR`, `SHIBOR_ON`, `TONA`, `EURUSD`, `CNY`, `JPY`, `INR`, `KRW` | India/Korea currently contribute FX but not a daily local-rate node. |
| `stationkeeping` | `READY` | `TGA`, `RRP`, `WALCL` | None. |
| `farbasin` | `READY_PARTIAL` | `PALIMPSEST_FEAR`, `PALIMPSEST_NEW`, `PALIMPSEST_GFI`, latest target board | Context works; model entry waits for 250 daily observations. |
| `moorings` | `READY` | Stablecoin board/total, `USDT_USD`, `BTC_USD` | None. |
| `cpsentinel` | `READY` | DeFiLlama hacks, `CP_SPREAD` | None. |
| `oilfunding` | `READY` | `WTI_SPOT`, `BRENT_SPOT`, `SOFR`, IORB, CP rates, `DGS3M`, `INR`, energy/core CPI, `CUSTODY_TSY`, `FOREIGN_RRP`, `CUSHING_STOCKS` | Live futures curve remains scenario-only because the old public EIA table ended. |
| `ballast` | `READY` | CFTC commodity positions, `WTI_SPOT`, `HENRY_HUB_SPOT`, `CRUDE_STOCKS_EX_SPR`, `SOFR`, IORB, `CP_NONFIN_3M`, `DGS3M` | EIA bulk can improve latency/resilience, not basic functionality. |
| `estuary` | `READY_PARTIAL` | 22 named H.10 FX rows; ESTR/SONIA/TONA/SHIBOR/India/Korea rates; `DXY_BROAD/AFE/EME`; 10 energy/industrial/food rows; USD funding, swap lines, foreign RRP, FIMA, BIS offshore credit | The ten added H.10 histories passed a local real sweep; production deployment is still pending. Four qualifying FX and commodity histories already suffice. |
| `windfetch` | `LIVE_ONLY` | Undertow current-affairs pack | No honest historical archive. |
| `sonar` | `READY_PARTIAL` | Every available FRED/OFR legacy series plus `SPREAD`, `TAIL`, `SRF`, `TGA`, crypto/stablecoin and Palimpsest | The H.10 expansion is locally filled; production verification remains. |
| `composite` | `READY` | Scores from tails, kink, weather, confession (`SRF/DW`), RV X-Ray, resonance, Hydrophone, Undertow, auctions, Warehouse, RRP buffers | Missing subscores are renormalized, never imputed as zero. |

Implementation wiring: `backend/seiche/assemble.py:428-1037`.

## Deep engine matrix

Every block below is returned under `payload.deep` and `/api/deep`; `book` also
has `/api/book`. `navigator` is a top-level sibling.

| Engine | Status | Exact input contract | Remaining data issue |
|---|---|---|---|
| `history` | `FORWARD_ONLY` | `SPREAD`, `TAIL`, `SRF`, `DW`, `RRP`, `RES_GDP`, RV X-Ray pair, auction digestion | Numeric reconstruction works; current/final vintages are not validated backtest evidence. |
| `tell` | `READY` | History index, `VIX`, `HY_OAS`, `IG_OAS`, `DGS10` | None. |
| `playbook` | `READY` | History index, Tell, `SP500`, `VIX`, `HY_OAS`, `IG_OAS`, `DGS10`, `DGS2`, `BTC_USD` | None. |
| `turn` | `READY` | `SPREAD`, `RRP`, `TAIL`, reserve/GDP percentile | None. |
| `backtest` | `UNAVAILABLE` | History percentile, `SPREAD`, outcomes, signed vintage evidence | Requires a content-bound ALFRED/as-published cut for all eight History inputs. |
| `microseism` | `READY` | `SPREAD` | None. |
| `leakaudit` | `READY` | All History inputs plus `SPREAD` | Evidence remains construction-PIT until the vintage gap closes. |
| `tidetables` | `READY` | `SPREAD`, `EFFR-IORB`, `BGCR-SOFR`, `TAIL`, `RRP`, TGA/reserve changes, `SRF` | None. |
| `swell` | `READY` | `SPREAD`, Undertow damping, auctions/upcoming auctions | None. |
| `bathymetry` | `READY` | `SPREAD` | None. |
| `markov` | `READY` | History index/regime, current composite regime | None. |
| `oujump` | `READY` | History index, current composite value | None. |
| `montecarlo` | `READY` | History index, current composite value | None. |
| `funding_pop` | `READY` | `SPREAD`, `RRP`, Undertow damping | None. |
| `riptide` | `ALIAS` | Exact compatibility alias of `funding_pop` | No independent source. |
| `gyre` | `READY` | `SPREAD` | None. |
| `refereegli` | `READY_PARTIAL` | `FED_ASSETS_LONG`, `ECB_ASSETS`, `BOJ_ASSETS`, `EURUSD_LONG`, `JPY_LONG`, `NASDAQ`, `INDPRO`, `TGA_LONG`, `RRP_LONG` | G3 scope works; qualifying PBoC/BoE balance-sheet feeds remain absent. |
| `ml` | `READY` | History primitives/index/percentile, `VIX`, `HY_OAS`, `DGS10`, `INR`, USDT peg, stable total, historical TED pretraining | Vintage boundary applies to retrospective claims. |
| `stacker` | `READY` | History percentile, ML, Tide Tables, Swell, Bathymetry, Tell; labels from `SPREAD` | Forward record must keep accruing. |
| `regatta` | `READY` | Stack OOS calibration, probabilities, labels | None. |
| `searoom` | `READY` | Stack OOS probabilities/labels, History regime | None. |
| `seastate` | `READY` | `SPREAD` | None. |
| `book` | `READY` | Stack streams, Tell; returns from `DGS2`, `DGS10`, `SP500`, `BTC_USD`, `TB3M`; PIT records | Live performance remains forward-only. |
| `modelcourt` | `READY` | Completed deep blocks plus the odds ledger | Continue immutable forward odds capture. |
| `navigator` | `UNAVAILABLE` | Whole board context pack, spread data date, configured model endpoint | Dated failure was no model endpoint; not a market-source gap. |

Deep wiring: `backend/seiche/assemble.py:1079-1490`, `2280-2285`, and
`2320-2336`. The vintage gate requires exactly `spread_bp`, `tail_bp`,
`srf_accepted`, `dw_b`, `rrp_b`, `res_gdp`, `pair_b`, and `digestion`; see
`backend/seiche/engines/history.py:43-90`.

## V2 market and universal-engine matrix

V2 computes policy-relative, corridor, secured/unsecured, term-slope,
funding-minus-bill, liquidity-drain, facility-usage, tail-dislocation, and
volume-dislocation components from semantic roles, not source mnemonics. The
API reads sealed canonical observations only.

| Product/pack | Required component and exact roles | Source status | Product status / next boundary |
|---|---|---|---|
| `US-USD` local gauge | `policy_relative_overnight`: `SOFR SECURED_OVERNIGHT` minus `IORB POLICY_TARGET` | Implemented official; extensive history | Numeric. `corridor_pressure` lacks `POLICY_FLOOR`; Global Tide lacks basis; historical prediction is forward-only. |
| `EA-EUR` local gauge | `policy_relative_overnight`: `ESTR UNSECURED_OVERNIGHT` minus `ECB_DFR POLICY_FLOOR` | ECB policy/benchmark/liquidity adapters implemented | Source-complete; canonical history and validation must accrue. |
| `UK-GBP` local gauge | `policy_relative_overnight`: `SONIA UNSECURED_OVERNIGHT` minus `BOE_BANK_RATE POLICY_TARGET` | Policy and SONIA adapters implemented; SONIA derived-only | Source-complete; RONIA would add the optional secured/unsecured component. |
| `JP-JPY` local gauge | `policy_relative_overnight`: `TONA UNSECURED_OVERNIGHT` minus `BOJ_BASIC_LOAN POLICY_CEILING` | BOJ rate/account adapters implemented | Source-complete; canonical history and validation must accrue. |
| `KR-KRW` local gauge | Required policy-relative BOK call minus base rate; optional corridor | BOK policy and call adapters implemented but individually issued API key required; all BOK adapters are `METADATA_ONLY` while redistribution review remains open | Rows can be collected into the private canonical store, but the public gauge stays unavailable until terms review closes; corridor also waits for BOK facility rows; pack remains reference/forward-only. |
| `CN-CNY` local gauge | `term_funding`: `DR007 TERM_1W` minus `SHIBOR_ON UNSECURED_OVERNIGHT` | CFETS adapter implemented but `METADATA_ONLY` | Public gauge unavailable until redistribution permission or a derived-entitled replacement exists. |
| `HK-HKD` local gauge | `liquidity_buffer_drain`: `HKMA_AGGREGATE_BALANCE SYSTEM_LIQUIDITY` | HKMA adapter implemented | Source-complete narrow gauge; HONIA/HIBOR/basis add breadth. |
| `IN-INR` local gauge | `corridor_pressure`: `CALL_WAR UNSECURED_OVERNIGHT` within `RBI_SDF POLICY_FLOOR` and `RBI_MSF POLICY_CEILING` | RBI adapter implemented; most optional official roles included | Source-complete; licensed TREPS/CP/CD/basis add breadth. |
| `AU-AUD` local gauge | `policy_relative_overnight`: `AONIA UNSECURED_OVERNIGHT` minus `RBA_CASH_TARGET POLICY_TARGET` | RBA cash/policy adapters implemented | Source-complete; secured overnight/BBSW optional. |
| `NZ-NZD` local gauge | `corridor_pressure` requires `UNSECURED_OVERNIGHT`, `POLICY_FLOOR`, `POLICY_CEILING` | RBNZ adapters policy-blocked; no unambiguous unsecured overnight role declared | Unavailable pending written automated-access approval and a valid overnight classification. |
| `SG-SGD` local gauge | `corridor_pressure`: `SORA UNSECURED_OVERNIGHT` within MAS floor/ceiling | MAS SORA/rate adapters implemented | Source-complete; CP3M adds optional term funding. |
| `GLOBAL` Tide | At least two aligned, public-derivable `FX_SWAP_BASIS` histories | No qualifying production basis series | Unavailable by contract; local gauges are never averaged into a substitute. |

The registry contains eleven market packs at
`backend/seiche/markets/registry.py:41-69`. Universal computation and
publication fail-closed at `backend/seiche/markets/materialize.py:266-390` and
`backend/seiche/markets/publication.py:30-64`.

### V2 declared capability status

This is the complete pack-catalog capability matrix. `READY` here means the
pack declares the role contract and minimum history; it does **not** claim that
the latest production sweep supplied fresh rows. The ten non-US packs remain
`REFERENCE`: each declares the same ten universal capabilities `UNAVAILABLE`
and historical prediction `FORWARD_ONLY`, even where its current calibration
can already produce a numeric forward-only component. `US-USD` remains
`VALIDATING`, not `SUPPORTED`.

| Capability / kernel | Exact semantic-role contract | `US-USD` declaration | All ten `REFERENCE` packs |
|---|---|---|---|
| `policy_relative_overnight` | Pack-selected `SECURED_OVERNIGHT` or `UNSECURED_OVERNIGHT` minus pack-selected `POLICY_TARGET`, `POLICY_FLOOR`, or `POLICY_CEILING` | `READY`: `SOFR SECURED_OVERNIGHT` minus `IORB POLICY_TARGET` | `UNAVAILABLE` in the catalog; market calibrations select their local roles |
| `corridor_pressure` | Pack-selected overnight role plus `POLICY_FLOOR` and `POLICY_CEILING` | `UNAVAILABLE`: no canonical US floor | `UNAVAILABLE` |
| `secured_unsecured` | `SECURED_OVERNIGHT` minus `UNSECURED_OVERNIGHT` | `READY`: SOFR and OBFR | `UNAVAILABLE` |
| `term_funding` | Either pack-selected `TERM_1W/TERM_1M/TERM_3M` minus its overnight role, or `CP_3M/CD_3M` minus `TBILL_3M` | `READY`: `CP_3M` and `TBILL_3M` | `UNAVAILABLE` |
| `liquidity_buffer_drain` | Four-native-observation negative change in `RESERVE_BALANCES` or `SYSTEM_LIQUIDITY` | `READY`: `WRESBAL RESERVE_BALANCES` | `UNAVAILABLE` |
| `facility_usage` | `CENTRAL_BANK_FACILITY_TAKEUP` divided by `RESERVE_BALANCES`, in matching canonical currency units | `READY`: SRF take-up and WRESBAL | `UNAVAILABLE` |
| `tail_dispersion` | `RATE_P99` minus `RATE_MEDIAN` for one local market | `READY`: SOFR p99 and median | `UNAVAILABLE` |
| `volume_dislocation` | `REPO_VOLUME` versus its prior same-weekday median, requiring eight seasonal observations | `READY`: SOFR repo volume | `UNAVAILABLE` |
| `reserve_kink` | `RESERVE_BALANCES`, `POLICY_TARGET`, and `SECURED_OVERNIGHT` | `READY`: WRESBAL, IORB, SOFR | Not declared |
| `calendar_amplification` | No executable universal-kernel role binding yet | Not declared | `UNAVAILABLE` |
| `cross_basin_coupling` | At least two aligned public-derivable `FX_SWAP_BASIS` histories | `UNAVAILABLE` | `UNAVAILABLE` |
| `historical_prediction` | No extra market role; requires point-in-time canonical observations and a forward validation record | `FORWARD_ONLY` | `FORWARD_ONLY` |

Authoritative declarations are `PACK.capabilities` in
`backend/seiche/markets/us_usd/pack.py:374-454` and
`pre_support_capabilities` in `backend/seiche/markets/reference.py:14-38`.
Executable role resolution is in `backend/seiche/kernel/engines.py:257-508`;
the nine materialized component kinds are enumerated in
`backend/seiche/markets/calibration.py:19-28`.

### KR-KRW forward-v1 calibration contract

The current worktree now contains the exact calibration that the audit
recommended. It deliberately makes only the component supported by the two
implemented BOK ECOS series mandatory:

| Component | Required | Weight | Roles | Parameters | Justification |
|---|---:|---:|---|---|---|
| `policy_relative_overnight` | yes | 0.65 | `CALL_OVERNIGHT_ALL UNSECURED_OVERNIGHT` minus `BOK_BASE_RATE POLICY_TARGET` | stress scale 15 bp; minimum history 20 | Both rows have production adapters. This can yield a useful internal gauge without pretending KOFR or corridor facilities exist; public redistribution remains gated on ECOS terms review. |
| `corridor_pressure` | no | 0.35 | `CALL_OVERNIGHT_ALL UNSECURED_OVERNIGHT` positioned between `BOK_LIQUIDITY_ADJUSTMENT_DEPOSIT POLICY_FLOOR` and `BOK_LIQUIDITY_ADJUSTMENT_LOAN POLICY_CEILING` | center 50; scale 25; minimum history 20 | Correct corridor semantics are declared, but the facility adapter is not yet implemented, so this component must remain optional/unavailable. |

Calibration evidence: `backend/seiche/markets/calibration.py:274-298`.
Instrument roles: `backend/seiche/markets/korea_krw/pack.py:142-175`.
Production adapter membership: `backend/seiche/sources/official.py:2185-2211`.
Contract test: `backend/tests/test_korea_krw_pack.py:35-48`.
The pack labels every ECOS adapter `METADATA_ONLY` because the official-source
review did not find a sufficiently clear stable redistribution grant. The JSON
ledger records `bok_ecos_api.rights_status` as `review_required`; an affirmative
review and a dedicated contract change are required before public computation
or row redistribution.

## Ranked top 20 data gaps

The JSON ledger is the machine-readable authority for rank, priority, blockers,
and candidate IDs.

| Rank | Priority | Gap | User-visible effect |
|---:|:---:|---|---|
| 1 | P0 | Acquire/licence and backfill at least two public-derivable `FX_SWAP_BASIS` histories | Turns Global Tide from null to numeric. |
| 2 | P0 | Create signed, content-bound ALFRED/as-published histories for all eight History inputs | Turns Backtest from UNVERIFIED to runnable and upgrades History/ML/Stack/Book evidence. |
| 3 | P0 | Obtain redistribution permission or a derived-entitled replacement for DR007 and SHIBOR | Makes China's required term-funding component publicly calculable. |
| 4 | P0 | Obtain written RBNZ automated-access approval and an unambiguously classified NZ unsecured overnight series | Makes New Zealand's required corridor component possible. |
| 5 | P0 | Persist/backfill and continuously materialize canonical histories for source-complete non-US packs | Converts implemented adapters into durable local gauges without buying duplicate data. |
| 6 | P1 | Map a defensible canonical US `POLICY_FLOOR` | Unlocks the sole missing domestic US corridor component. |
| 7 | P1 | Implement BOK liquidity-adjustment deposit/loan collection | Activates Korea's optional corridor component. |
| 8 | P1 | Obtain KOFR redistribution rights or a derivable licensed substitute | Adds Korea secured/unsecured stress. |
| 9 | P1 | Feed canonical RBI `CALL_WAR` and BOK `CALL_OVERNIGHT_ALL` into legacy Harbors/Estuary/Spillover | Replaces two-month-lagged OECD mirrors in three published engines. |
| 10 | P1 | Backfill/accumulate SHIBOR and `CN_FDR007` past 60 observations | Unlocks China z/percentile scoring in Basins and Harbors. |
| 11 | P1 | Accumulate/backfill Palimpsest to 250 daily observations | Moves Far Basin from context-only to model-eligible. |
| 12 | P1 | Persist per-row observation clocks for NY Fed, Treasury and CFTC table envelopes | Prevents fresh HTTP fetches from masking stale tables. |
| 13 | P1 | Deploy and independently verify the completed ten-currency H.10 backfill on Hetzner | Immediately thickens Estuary and Sonar in production. |
| 14 | P1 | Ingest SEC N-MFP monthly bulk data | Adds MMF holdings, repo counterparties, concentration, and portfolio structure to Money Market/Warehouse. |
| 15 | P1 | Ingest Treasury TIC country/holder tables | Deepens Official Bid, Warehouse, Estuary, and Referee cross-border evidence. |
| 16 | P1 | Add entitled/derived HONIA | Adds a real HK overnight-rate leg beyond Aggregate Balance. |
| 17 | P1 | Add entitled/derived RONIA | Completes the UK secured/unsecured wedge. |
| 18 | P2 | Add an entitled Australian secured-overnight benchmark | Completes AU secured/unsecured context. |
| 19 | P2 | Add entitled CCIL TREPS | Completes India secured/unsecured and reporting-turn context. |
| 20 | P2 | Add HK HIBOR 1W/1M/3M derived data | Adds Hong Kong term-funding shape. |

India CP/CD, Singapore CP, Australia BBSW, Korea CD/CP/KORIBOR, and New Zealand
bank bills follow these twenty. Canada CORRA and Brazil Selic are strong next-pack
candidates, not prerequisites for repairing a current null.

## Avoid low-return acquisition

- Do not replace IOER or TED; both discontinuations are intentionally modeled.
- Do not delay Estuary for gold, a live EIA futures strip, or Cushing capacity.
  The qualifying free gold series ended, EIA's old futures table stopped in
  2024, and capacity is a dated structural reference.
- Do not buy duplicate required data for EA, UK, JP, HK, IN, AU, KR, or SG before
  canonical scheduling/history/materialization is verified on the server.
- Do not add a market source to fix Navigator; configure its model endpoint.
- Do not silently relabel public forward points or premia as covered-interest-
  parity basis. Global Tide requires an actual qualifying basis series.

## Maintenance contract

Update this ledger and `data-source-expansion.json` together when any of the
following changes:

- a collector becomes implemented, policy-blocked, or retired;
- redistribution or automated-access review changes;
- a required local calibration changes;
- an engine becomes numeric, degraded, or unavailable;
- the top-gap ordering changes after live production coverage is re-measured.

Run the consistency check with:

```bash
pytest -q backend/tests/test_data_source_expansion_ledger.py
```
