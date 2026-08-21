# Money Market Desk v1

Status: implementation plan and release contract
Owner: Seiche
Initial markets: global atlas, with US dollar institutional-depth reference
Decision date: 2026-08-21

## Product decision

Seiche will become the place where a reader can understand the world's
short-term funding systems without translating dozens of central-bank
releases, market-data pages, local conventions, and news stories by hand.

The first release has two connected surfaces:

- the **Global Money-Market Atlas**, which compares every declared market in
  its native convention and states exact live-data gaps; and
- the **USD Funding Desk**, the institutional-depth reference implementation
  covering policy rates, unsecured and secured funding, repo distribution and
  volume, Treasury cash, reserve balances, central-bank facilities,
  commercial paper, bills, and money-market funds.

Both surfaces serve two readings at once:

1. a plain-language answer to “what changed and why does it matter?”; and
2. the underlying values, clocks, conventions, formulas, history, and
   counter-case a rates or treasury professional needs to audit that answer.

This is not a promise of a free Bloomberg terminal. Public official data is
excellent for structural funding intelligence, but most of it publishes at
daily, weekly, or monthly cadence. Real-time Treasury depth, when-issued
marks, futures, dealer runs, institutional certificates of deposit, and full
news text require licensed feeds. Seiche must identify those gaps instead of
manufacturing substitutes.

## Success criteria

The desk is useful when a reader can answer these questions in under two
minutes:

- Where did SOFR, EFFR, TGCR, and BGCR clear relative to the Fed's administered
  rates?
- Where did €STR, SONIA, TONA, SHIBOR, WACR/call money, HONIA, SORA, AONIA,
  and other local benchmarks clear relative to their own policy anchors?
- Was pressure broad or concentrated in the upper tail of repo transactions?
- Did volumes confirm the rate move, and in which repo segment?
- Did unsecured borrowers reprice relative to matched Treasury bills?
- Are reserves, the Treasury General Account, ON RRP, or Fed facilities
  changing the available cash backdrop?
- Are money-market funds moving cash between the Fed, FICC-sponsored repo,
  bills, CP, or other assets?
- Which observation is fresh, which is stale, and when should it update next?
- What evidence argues against the desk's current interpretation?

Product targets for v1:

- every displayed number has a value, unit, observation date, source, cadence,
  and a one-sentence explanation;
- every derived number has a formula identifier and alignment rule;
- missing evidence renders as unavailable, never zero or calm;
- public endpoints read already assembled USD output or already collected
  canonical observations and perform no network collection on request;
- the initial page adds no input or weight to the existing Seiche composite;
- monthly or weekly markets remain monthly or weekly; the UI never upsamples
  them into a false daily line;
- daily chart history is bounded to roughly 180 observations and monthly
  history to 36 observations so the static-first board remains fast;
- desktop and mobile views preserve keyboard navigation, visible focus, and
  reduced-motion behavior.

## User journeys

### Morning funding read

The reader opens `#money-markets`, reads the plain-language state, then checks
the strongest signal and counter-case. The clearing ladder shows market rates
against IORB and the lower/upper policy anchors. Distribution and volume cards
show whether the move is broad, tail-heavy, or thin.

### Quant audit

The reader switches from the interpretation to the metric table. Each metric
shows level, change, robust z-score, empirical percentile, exact formula,
alignment convention, and source clock. Charts expose the same series used by
the calculation. No screen-only transformation is permitted.

### Event follow-up

The reader moves from an auction, tax date, FOMC decision, or facility use to
the relevant money-market history. A later release can add a typed event
timeline and alert subscriptions without changing the metric contract.

### Agent and newsroom use

The REST and MCP surfaces return the same compact interpretation, evidence,
counter-case, metrics, sources, and timestamp contract used by the web page.
Newsroom prose may summarize those fields but may not introduce unsupported
numbers or causal claims.

## Information architecture

```text
USD FUNDING DESK
|
+-- State now
|   +-- plain-language read
|   +-- quant read
|   +-- strongest signal
|   `-- counter-case
|
+-- Clearing ladder
|   +-- policy anchors: ON RRP / IORB / SRF or target ceiling
|   `-- market prints: EFFR / SOFR / TGCR / BGCR / segment rates
|
+-- Secured funding
|   +-- SOFR percentiles and distribution width
|   +-- repo rates by venue
|   `-- volumes and venue shares
|
+-- Unsecured funding and cash curve
|   +-- financial and nonfinancial CP
|   +-- CP minus matched bill
|   `-- 4-week / 3-month bill curve
|
+-- Liquidity balances and facilities
|   +-- reserve balances / TGA / ON RRP
|   +-- SRF / discount window
|   `-- weekly changes and accounting bridge
|
+-- Money-market funds
|   +-- total assets / repo lending
|   +-- FICC-sponsored repo / Fed RRP shares
|   `-- later: fund-level flows, liquidity, maturity, and concentration
|
`-- Audit rail
    +-- data health and next expected update
    +-- formulas and alignment rules
    +-- sources and licence notices
    `-- known gaps
```

The global route sits one level above it:

```text
GLOBAL MONEY-MARKET ATLAS
|
+-- Global read / strongest divergence / counter-case
+-- Comparable pressure map (local percentile, never raw-rate ranking)
+-- Americas
+-- Euro area + United Kingdom + Europe
+-- India + South Asia
+-- China + Hong Kong
+-- Japan + Korea
+-- Singapore + ASEAN
+-- Australia + New Zealand
+-- Gulf / Middle East
+-- Africa
+-- Latin America
`-- Coverage ledger: live / delayed proxy / blocked / planned
```

## Global atlas contract

The atlas does not force different systems into a single interest-rate scale.
Each market row carries:

- market, monetary area, jurisdiction, currency, timezone, and settlement
  calendar;
- policy regime (`floor`, `corridor`, `tiered`, `quantity_targeting`,
  `exchange_rate_targeting`, `currency_board`, or explicitly unclassified);
- primary overnight benchmark, secondary secured/unsecured benchmark, policy
  floor/target/ceiling, and local naming;
- value, unit, event date, publication time, cadence, expected next update,
  source, revision state, and confidence;
- policy-relative spread only when rates share a compatible convention and
  alignment rule;
- native-frequency 1/5/20-observation changes, trailing robust z-score,
  empirical percentile, and volatility;
- local-currency-per-USD move and rate/FX correlation only on genuinely
  overlapping observations;
- a simple explanation, quant interpretation, counter-case, and known gaps.

Cross-market comparison uses each metric's own trailing distribution:

```text
local_pressure_percentile_i(t) = F_i,t(metric_i,t)
```

Raw overnight rates are never ranked as “more stressed” across countries.
Policy-relative spreads may be compared when definitions are compatible;
otherwise the atlas compares signed local percentiles and labels the metric.

### Priority market map

| Region / market | Local instruments to cover | Local mechanics that must remain visible |
|---|---|---|
| United States | SOFR, EFFR, OBFR, TGCR, BGCR, IORB, ON RRP, SRF, bills, CP, repo, MMFs | floor system, repo distribution, Treasury cash, reserve scarcity |
| Euro area | €STR, ECB deposit/main-refi/marginal-lending rates, €STR volume/dispersion, Euribor, repo, excess liquidity | corridor/floor transition, TLTRO, fragmentation, TARGET balances |
| United Kingdom | SONIA, Bank Rate, SONIA index/volume, gilt repo, T-bills, MMFs | reserve-remuneration framework, APF/QT cash effects, sterling turn dates |
| India | WACR, TREPS, market repo, MSF/SDF/repo rate, CRR, VRR/VRRR, T-bills, CDs/CP, durable-liquidity operations | corridor, reserve maintenance, government cash, RBI operations, INR funding transmission |
| China | DR001/DR007, R001/R007, SHIBOR, PBoC OMO/MLF/LPR anchors, pledged repo volume, CDs | dual interbank/exchange markets, bank/non-bank segmentation, managed liquidity |
| Japan | TONA, BoJ policy rate, call volumes, GC repo, T-bills, current-account balances | tiered reserves, JGB collateral, quarter/fiscal-year turns |
| Hong Kong | HONIA, HIBOR curve, base rate, aggregate balance, discount windows, CNH HIBOR | currency-board convertibility zone and HKD/CNH split |
| Singapore | SORA, MAS standing-facility rates, MAS bills/T-bills, SGD swap/FX funding | exchange-rate policy regime and domestic-liquidity operations |
| Australia | AONIA/cash rate, RBA target/ES rate, repo, bank bills, exchange-settlement balances | corridor/floor mechanics and bill/OIS transmission |
| New Zealand | NZIONA/OCR, settlement cash, RBNZ facilities, bills | floor/corridor operations and small-market turn effects |
| Korea | call rate, BoK base rate, RP, CDs/CP, reserve balances | reserve periods and bank/non-bank funding segmentation |
| Canada | CORRA, target/Bank Rate/deposit rate, repo, T-bills, bankers' acceptances transition | operating band and CORRA methodology |
| Switzerland | SARON, SNB policy rate, sight deposits, repo | tiered reserve remuneration and CHF safe-haven flows |
| Nordics | SWESTR/NOWA/DESTR and local policy facilities | separate calendars, currencies, and reserve systems |
| Gulf | DONIA, KONIA, OMIBOR, operating targets and central-bank facilities; indicative fixings only with their rights/methodology limits | USD pegs, oil cash, local interbank depth |
| Latin America | Effective Selic, Overnight TIIE Funding, IBR, TIB/TMM and local repo/facilities | transaction-versus-quote distinctions, reserve rules, onshore/offshore splits |
| Africa | ZARONIA, JIBAR-transition instruments, interbank/repo benchmarks and facilities | sparse publication, FX segmentation, and local settlement constraints |

“Every market possible” is managed as a coverage ledger, not an unsupported
claim. As of 2026-08-21 the discovery universe contains 63 monetary areas: 11
registered packs and 52 additional source-audited candidates across nine
regions. Currency unions are represented once, and territories sharing a
monetary authority are not duplicated. Each candidate records its benchmark
type, official authority and link, rights/access caveat, confidence, integration
stage and verification date; it carries no quote until a canonical pack has
passed its gates. A market can be:

- `INSTITUTIONAL`: full benchmark, policy, volume, distribution, liquidity,
  term curve, calendar, and revision coverage;
- `CORE`: benchmark, policy anchor, FX, history, and source clocks;
- `REFERENCE`: declared market pack with partial or delayed official inputs;
- `BLOCKED`: no legally and operationally reliable feed, with the blocker and
  last verification date published.

The expansion stages are deliberately separate from live-market states:

- `SOURCE_VERIFIED`: the primary authority and benchmark identity are known;
- `ACCESS_REVIEW`: source identity is known but licensing, sanctions, endpoint,
  or continuity approval is outstanding;
- `METHODOLOGY_REVIEW`: the candidate may be a proxy, mixed-tenor series,
  facility rate, or indicative fixing rather than a traded overnight benchmark;
- `RESEARCH_QUEUE`: even the stable benchmark/API definition needs more work;
- `COMPLIANCE_BLOCKED`: source integration is prohibited pending explicit legal
  and security clearance.

The atlas always renders all declared markets, including unavailable rows, so
missing coverage cannot disappear from the product roadmap.

## Metric contract

Every metric is a typed observation, not a naked scalar:

```json
{
  "id": "sofr_iorb_spread",
  "label": "SOFR minus IORB",
  "value": -4.0,
  "unit": "bp",
  "asof": "2026-08-20",
  "published_at": null,
  "expected_next_update": "next New York business day, about 08:00 ET",
  "cadence": "daily, T+1",
  "source": "Federal Reserve Bank of New York + Federal Reserve Board",
  "source_url": "https://www.newyorkfed.org/markets/reference-rates/sofr",
  "source_tier": "official",
  "revision_status": "latest known vintage",
  "formula": "100 * (SOFR_pct - IORB_pct)",
  "formula_version": "mm.usd.spread.v1",
  "alignment": "same observation date; no cross-clock fill",
  "change_1d": 1.0,
  "change_5d": -2.0,
  "change_20d": 0.0,
  "robust_z_1y": 0.35,
  "percentile_3y": 54.2,
  "confidence": "high",
  "status": "normal",
  "explanation": "Positive values mean secured overnight cash cleared above the Fed's reserve rate."
}
```

The canonical v2 observation store remains the long-run source of truth. The
v1 desk initially derives from the already collected, cached source series so
it can ship without weakening the sealed market-pack path. Migration is
complete only when the desk can be reconstructed from bitemporal canonical
observations with these clocks:

- `event_time`: the market or accounting period measured;
- `source_publication_time`: when the publisher released the value;
- `knowledge_time`: when Seiche could first have known it;
- `revision_id`: the exact source revision or content identity;
- `sealed_at`: when Seiche bound the product to its evidence record.

## Quant definitions

### Policy-relative rates

For rate `r` quoted in percent and policy anchor `a` quoted in percent:

```text
spread_bp(r, a) = 100 * (r - a)
```

Core spreads are EFFR-IORB, SOFR-IORB, SOFR-EFFR, SOFR-TGCR, BGCR-TGCR,
DVP-repo minus tri-party repo, and financial/nonfinancial CP minus a matched
3-month Treasury rate. Two observations are combined only on an exact common
event date unless a metric explicitly declares an as-of join.

### SOFR distribution

```text
IQR_bp       = 100 * (P75 - P25)
upper_tail_bp = 100 * (P99 - median)
full_tail_bp  = 100 * (P99 - P1)
tail_skew     = (P99 - median) / max(median - P1, epsilon)
```

SOFR is a broad secured overnight benchmark, not a pure general-collateral
rate. Tail measures are distribution diagnostics, not direct probabilities of
default or a claim about a single venue.

### Empirical context

For a current value `x_t` and a trailing sample known at `t`:

```text
percentile_3y = 100 * empirical_CDF_last_3_native_years(x_t)
robust_z_1y   = (x_t - median_last_native_year)
                / (1.4826 * MAD_last_native_year)
```

If the median absolute deviation is zero and the latest value equals the
median, robust z is zero; otherwise it is unavailable. Expanding
or trailing calculations must be recomputed on truncated samples in tests to
prove that a past row does not change when future data arrives. A native year
is 252 observations for a daily business-day series and 52 for a weekly
series; no slow series is padded to reach a daily row count.

### Repo venue decomposition

```text
cleared_premium_bp = 100 * (DVP_overnight_rate - tri_party_overnight_rate)
venue_share        = venue_volume / sum(available_venue_volumes)
volume_change_pct  = 100 * (volume_t / volume_t-k - 1)
```

Preliminary OFR repo observations retain their preliminary label. Aggregate
changes and ranks use only dates with the same reported component set as the
latest eligible date. A missing venue therefore reduces coverage and changes
the comparison mask; it is never silently treated as zero or allowed to create
a mechanical move in the total.

### Liquidity balances

Balance-sheet stocks are not added into an undocumented “net liquidity”
number. The desk shows each stock and its weekly change. A later accounting
bridge may publish:

```text
delta_reserves ~= delta_Fed_assets
                 - delta_currency
                 - delta_TGA
                 - delta_reverse_repo_liabilities
                 - delta_other_liabilities_and_capital
```

The residual must be displayed, the sign convention must be tested, and the
bridge must be labeled an accounting reconciliation rather than a forecast.

### Descriptive regime

The desk regime is context-only and must never feed the production composite.
Each eligible stress-oriented metric first maps to a raw trailing empirical
percentile. With `m` non-stale eligible channels, Seiche then applies the
dependence-robust Bonferroni upper-tail adjustment:

```text
adjusted_tail_probability = min(1, m * (1 - raw_percentile / 100))
adjusted_percentile       = 100 * (1 - adjusted_tail_probability)
```

The headline thresholds apply to the adjusted percentile:

- `NORMAL`: below the 75th percentile;
- `WATCH`: 75th to below 90th;
- `STRAIN`: 90th to below 97.5th;
- `STRESS`: 97.5th or above.

The page reports the selected component, its raw and adjusted ranks, family
size, sample length, and the counter-case. Reserve balances and ON-RRP remain
visible plumbing context but do not enter the headline selector because slow
policy-driven stock depletion is not itself a calibrated funding dislocation.
Freshness is measured against an
explicit live-response or historical-replay evaluation date, never against the
latest row inside the same frozen snapshot. Stale observations stay visible as
historical context but cannot set the regime; if every rankable component is
stale, the state is `CANNOT_ASSESS`. Metrics whose economic stress direction is
negative invert their percentile explicitly. The adjustment controls a family
of upper-tail flags under arbitrary dependence; it does not turn an empirical
rank into an event probability. Thresholds are configuration, not statistical
truth, and must be versioned before they affect alerts.

## Source plan

### Release 1: existing official collectors

| Domain | Publisher | Cadence | Initial fields |
|---|---|---:|---|
| Reference rates | New York Fed | daily T+1 | SOFR/TGCR/BGCR rates, P1/P25/P75/P99, volume |
| Policy | Federal Reserve | daily/as changed | IORB and target/SRF ceiling |
| Fed funds | New York Fed via FRED | daily T+1 | EFFR |
| Repo venues | OFR STFM | daily, preliminary | DVP/tri-party/GCF rates and volumes |
| Facilities | New York Fed/Fed | daily/weekly | SRF, ON RRP, discount window |
| Balances | Fed H.4.1/Treasury DTS | weekly/daily T+1 | reserves, Fed assets, TGA, currency |
| Commercial paper | Federal Reserve via FRED | daily T+1 | 3-month AA financial/nonfinancial rates |
| Bills | Treasury/Fed via FRED | daily | 4-week and 3-month rates |
| MMFs | OFR STFM | monthly | total assets and repo with FICC/Fed/total |
| Treasury events | FiscalData/TreasuryDirect | event-driven | auction results, upcoming settlements |

Release 1 reuses Seiche's existing collectors. This reduces operational risk
and gives the new surface the same cache, stale fallback, provenance, and
point-in-time behavior as the rest of the board.

### Release 2: official depth

- Add EFFR and OBFR distributions directly from the New York Fed reference
  rates API, including volumes and percentiles.
- Add SOFR 30-, 90-, and 180-day averages and index.
- Add the Treasury's complete daily bill curve and security-level auction and
  buyback results: CUSIP, offering, tenders, accepted, bidder shares,
  settlement, maturity, and acceptance ratios.
- Add Federal Reserve CP issuance volumes, outstanding amounts, and A2/P2
  rates. Do not claim daily CD coverage from confidential FR 2420 data.
- Add New York Fed primary-dealer financing, fails, and maturity-bucket
  positions to the secured-funding page.
- Ingest SEC Form N-MFP/N-MFP3 flat files with fund, share class, holding,
  counterparty, collateral, maturity, liquidity, WAM/WAL, NAV, yield, and flow
  dimensions.

### Global official-source expansion

Adapters are added market by market, with current Seiche packs providing the
schema and calendar boundary. Priority authorities include the ECB/Eurostat,
Bank of England, RBI/CCIL, CFETS/PBoC, Bank of Japan, HKMA, MAS, RBA, RBNZ,
Bank of Korea, Bank of Canada, SNB, Sveriges Riksbank, Norges Bank, Danmarks
Nationalbank, BIS, and national debt-management offices.

The acceptance gate for any adapter is:

1. primary or contractually permitted source;
2. stable machine-readable access or a reviewed resilient parser;
3. exact unit, day-count, compounding, tenor, calendar, and publication clock;
4. redistribution policy stored with the adapter;
5. historical depth sufficient for the claimed statistic;
6. raw-capture hashing, revision handling, and stale fallback;
7. point-in-time tests and a public coverage statement.

An OECD or FRED mirror may seed a `REFERENCE` market, but it cannot earn
`CORE` or `INSTITUTIONAL` status when its cadence or lineage is weaker than the
local official benchmark.

### Release 3: expectations and licensed extensions

- Use CFTC Part 43 SDR transaction reports for an indicative, correction-aware
  OIS curve and activity view. Capped/rounded notionals and cancelled trades
  stay visible in the quality metadata.
- Add CME Fed Funds/SOFR futures only under a valid display, non-display,
  derived-data, and redistribution agreement. ZQ, SR1, and SR3 have different
  settlement conventions and require separate formulas.
- Add DTCC Money Market Kinetics only under licence for institutional CD and
  CP intraday coverage.
- Add executable Treasury and repo data only through a feed whose contract
  permits the intended user and API distribution.

## News and explanation layer

Replacing a subscription is not only a charting problem. Seiche's Rissaga
radar, daily dispatch, articles, Telegram delivery, and source-grounding rules
form the initial newsroom. They should evolve into a typed event ledger:

```text
source item -> normalized event -> entity/instrument links -> evidence bundle
            -> story cluster -> revision timeline -> desk interpretation
```

An event record needs:

- source URL, publisher, publication time, retrieval time, and content hash;
- event type such as policy decision, auction, facility operation, data
  release, methodology change, outage, correction, or market dislocation;
- affected instruments, tenors, counterparties, currencies, and calendars;
- numeric claims linked to metric IDs and exact observation vintages;
- status (`developing`, `confirmed`, `corrected`, `closed`);
- thesis, evidence, counter-case, confidence, and what-to-watch-next;
- story-cluster identity so ten rewrites do not become ten alerts.

The web roadmap is: live event tape, story page with revisions, instrument and
topic pages, source comparison, full-text search over Seiche-authored content,
watchlists, email/Telegram/webhook alerts, and a versioned news API. Copyrighted
publisher text is linked and summarized within licence limits, never mirrored.

## System design

```text
Official/licensed sources
        |
        v
independent collectors -- raw immutable capture + content hash
        |
        v
normalizers ----------- units, clocks, identifiers, licence policy
        |
        v
canonical observations (event/publication/knowledge time)
        |
        +--> data-quality gates --> quarantine + operator alert
        |
        v
money-market analytics --> sealed detail product + compact desk product
        |                                    |
        +------------------+-----------------+
                           v
                  REST / MCP / static export
                           |
                  +--------+--------+
                  v                 v
             web terminal      newsroom/alerts
```

The present FastAPI + React process is sufficient for release 1 because the
collectors already run outside request handling and the terminal is
static-first. As coverage grows, the boundaries become:

- collector workers by source cadence and rate limit;
- PostgreSQL for canonical metadata and bitemporal queries;
- immutable object storage for raw captures and Parquet partitions;
- a materialization worker for versioned desk products;
- an event bus only when more than one consumer needs the same update;
- a search index for the news/event corpus, never as the source of record.

## API contracts

Release 1 adds compact public endpoints:

```text
GET /api/v2/money-markets  # canonical global atlas
GET /api/money-markets     # deep USD desk
```

The global route reads already collected canonical observations and returns
`seiche.global-money-markets.v1`; the USD route returns the already assembled
`seiche.money-market-desk.v1` product. Both use public cache headers. Neither
may collect, fit a model, or wait on a rebuild. The existing full overview
continues to carry the USD engine for static-first UI compatibility. Future v2
routes will bind deeper local detail to sealed products:

```text
GET /api/v2/markets/US-USD/money-market-desk
GET /api/v2/markets/US-USD/money-market-desk/asof/{knowledge_timestamp}
GET /api/v2/markets/US-USD/instruments/{instrument_id}
GET /api/v2/events?instrument=&since=&status=
```

The market-specific v2 route generalizes to every registered pack:

```text
GET /api/v2/markets/{market_id}/money-market-desk
GET /api/v2/markets/{market_id}/money-market-desk/asof/{knowledge_timestamp}
```

Pagination, cache validators, explicit schema versions, licence-aware value
redaction, and knowledge-time queries follow the existing market API rules.

## Reliability and data quality

Quality gates run before publication:

- schema and identifier validation;
- unit and sign-convention validation;
- monotonic event/publication/knowledge clocks;
- duplicate and revision checks;
- expected-cadence and holiday-aware freshness;
- rate bounds, non-negative volumes, and balance scale checks;
- exact-date alignment tests for spreads;
- preliminary/final status preservation;
- distribution ordering `P1 <= P25 <= median <= P75 <= P99`;
- deterministic JSON serialization with no NaN or infinity;
- truncation equality for every historical derived row;
- public redistribution-policy enforcement.

Operational targets after the canonical v2 product ships:

- 99.9% monthly availability for cached public reads;
- p95 cached API latency below 250 ms;
- official-source update visible within 15 minutes of successful collection;
- stale state shown within one missed expected publication window;
- no silent use of a last-known-good value after its stale threshold;
- raw capture, normalized observation, and published product hashes retained
  for every release.

## Security and legal controls

- source credentials are server-side secrets and never enter static payloads;
- public routes are allowlisted in the curated OpenAPI document;
- query limits, payload bounds, timeouts, and cursor validation apply to
  series and search endpoints;
- source terms and redistribution status live with each adapter: `derived_only`
  can expose approved non-reversible statistics while withholding raw values,
  whereas `prohibited` omits the adapter, source metadata, values, and faults
  from public payloads entirely;
- New York Fed attribution and non-affiliation notices remain visible;
- CME, DTCC, ICI, and other licensed data remain disabled until the exact
  display, derived-data, and API rights are documented;
- the terminal states “Research data, not investment advice.”

## Frontend direction

The atlas and desk inherit Seiche's abyssal black, blurple instrument light, calm-to-
stress status ramp, Inter display face, and JetBrains Mono data face. The one
new signature is the **clearing ladder**: a shared vertical rate scale where
policy anchors are rails and market prints are movable markers. It makes a
basis-point relationship visible before a reader parses a table.

Desktop layout:

```text
+------------------------------------------------------------------+
| state / plain read / quant read / strongest signal / countercase |
+----------------------+----------------------+--------------------+
| clearing ladder      | SOFR distribution    | update clock       |
+----------------------+----------------------+--------------------+
| secured rates + vol  | unsecured + bills    | liquidity stocks   |
+----------------------+----------------------+--------------------+
| money funds          | charts / history                          |
+----------------------+-------------------------------------------+
| formulas, source links, caveats, unavailable coverage            |
+------------------------------------------------------------------+
```

The atlas uses a second signature: a **clock-aware harbor grid**. Each market
is positioned by region, but its pulse shape encodes cadence (daily, weekly,
monthly) and its label always carries the local benchmark. Color describes
the market's own percentile state, never the level of its interest rate.

On mobile the interpretation remains first, the ladder becomes a horizontal
scroll-safe scale, tables expose fewer columns with metric detail below, and
source/formula text wraps rather than truncates. Motion is limited to a single
load transition and hover/focus responses; reduced-motion users receive the
same information without animation.

## Delivery plan

### R1 — global atlas + USD Funding Desk foundation

- pure context engine over existing source series;
- global atlas over every declared market, retaining honest unavailable rows;
- native-frequency analytics for euro area, UK, China, Japan, India, Korea,
  and every other currently wired series;
- source-bound metric contract and descriptive regime;
- clearing ladder, section tables, bounded history, glossary, and caveats;
- public REST discovery and focused tests;
- deploy through the signed, gated Hetzner workflow.

### R2 — official-data depth and Asia/Europe institutional packs

- direct full NY Fed reference-rate family;
- Treasury bill curve, auction and buyback ledger;
- Fed CP rates, issuance and outstanding;
- SEC N-MFP fund/holding/counterparty warehouse;
- typed event ledger and money-market event tape;
- sealed v2 detail/as-of products.
- direct daily adapters and full local policy/liquidity contracts for India,
  euro area, UK, China, Japan, Hong Kong, Singapore, Australia, New Zealand,
  Korea, Canada, and Switzerland;
- explicit reference/core/institutional promotion gates for every market.

### R3 — remaining regions, personalization, and distribution

- saved watchlists and metric/event alerts;
- entity, instrument, tenor, and topic pages;
- search, story revision timelines, webhooks, newsletter editions;
- newsroom grading: timeliness, novelty, correction rate, citation coverage,
  alert precision, and reader follow-through.
- Nordic, Gulf, Latin American, and African market packs where authoritative
  access passes the adapter gate; blocked markets stay public in the ledger.

### R4 — licensed professional tier

- execution-grade Treasury, futures/OIS, CP/CD, and repo feeds under explicit
  rights;
- meeting-adjusted policy-expectation curves, carry/roll, forwards, scenario
  shocks, and portfolio exposure overlays;
- enterprise entitlements, audit export, and support SLOs.

## Deployment and rollback

Release 1 follows the repository's existing path:

1. run focused engine, API, public-contract, and frontend builds locally;
2. run the full backend test suite and public-surface guard;
3. push a reviewed commit to a feature branch and pass pull-request checks;
4. merge to `main`, which invokes the signed Hetzner deployment workflow;
5. require the candidate process to rebuild and seal both US market products;
6. verify `/api/health?require_rebuilt=true`, `/api/v2/money-markets`,
   `/api/money-markets`, the static snapshot, and the `#money-markets` route
   externally;
7. if any gate fails, retain the last-known-good snapshot and roll back using
   the existing release controller rather than editing production in place.

## What to revisit as Seiche grows

- Split the detail payload from the board once its compressed size or parse
  cost affects first paint.
- Move descriptive regime thresholds into an explicitly reviewed calibration
  artifact before alerts depend on them.
- Replace legacy-source derivation with canonical observation materialization
  once full source coverage exists in the market pack.
- Add a message bus only when collector events need multiple independent
  consumers; a queue is premature while one materializer owns the update.
- Evaluate columnar analytics or a time-series extension only after measured
  PostgreSQL query and retention limits, not from anticipated scale.
- Treat licensed-feed procurement as a product decision tied to paying users,
  not a technical shortcut around public-data limitations.
