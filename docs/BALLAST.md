# Ballast: the commodity funding spine

Ballast is the energy-futures cash-pressure layer inside **Oil × Funding**. It
does not forecast commodity prices and it does not enter Seiche's composite.
It asks a narrower question:

> Is an observed commodity move large enough, concentrated enough, and landing
> in expensive enough funding conditions to deserve attention as a cash event?

The first release covers WTI physical crude and Henry Hub natural gas. That
small contract set is intentional: both have stable CFTC contract identifiers,
public benchmark histories, physical settlement anchors, and economically
meaningful contract multipliers. Breadth comes after the method is trustworthy.

## What makes it different

Most commodity screens stop at price, volatility, or speculative net length.
Ballast connects four separate ledgers without blending away their meaning:

1. **Mark displacement**: public benchmark movement × CFTC open interest × the
   exchange contract multiplier.
2. **Paying-side concentration**: the CFTC long or short concentration field
   selected according to the direction of the observed benchmark move.
3. **Physical collateral**: EIA commercial crude stocks, their weekly change,
   public benchmark value, and a SOFR carry benchmark.
4. **Funding landing zone**: SOFR−IORB and nonfinancial CP−Treasury spreads.

Every channel is ranked against its own trailing history. The headline is the
worst available **commodity or physical** percentile; funding is a separate
amplifier overlay and cannot trigger the commodity state by itself. Nothing is
collapsed into a weighted commodity score.

## Evidence classes

| Class | Ballast examples | What the product may say |
|---|---|---|
| Observed | CFTC futures-only open interest and trader classes; EIA stocks; public spot benchmarks; SOFR, IORB, CP and Treasury rates | The value, observation date, cadence and source. |
| Derived | Gross mark-displacement scale; category paying-side scales; inventory benchmark value; percentile rank | The exact identity and its units, next to its limitations. |
| Scenario-only | Exchange initial/maintenance-margin schedules until a stable contract-level feed is available | A user-supplied or explicitly versioned assumption, never an observation. |
| Dark | OTC positions, bilateral collateral terms, portfolio netting, client add-ons and named participant books | `dark` or `cannot assess`, never zero. |

## Contract identity

For contract `c` on two adjacent CFTC report Tuesdays:

```text
gross_mark_displacement[c,t]
  = abs(public_spot_proxy[c,t] - public_spot_proxy[c,t-1])
    × open_interest_contracts[c,t]
    × contract_multiplier[c]
```

WTI uses 1,000 barrels per contract. Henry Hub uses 10,000 MMBtu per contract.
Open interest counts each matched contract once, so the result is the scale one
side would pay if the spot proxy tracked the relevant futures settlement.

This is **not an observed variation-margin call**. The exact settlement, the
month distribution of open interest, calendar-spread offsets, portfolio
netting, OTC hedges and clearing-member add-ons are not in the public inputs.

Category proxies apply the same price movement to the reported category on the
paying side:

- a rising proxy selects reported shorts;
- a falling proxy selects reported longs;
- an unchanged proxy has no paying-side direction.

The producer/merchant row is therefore a commercial-side sensitivity proxy,
not a claim about producers' net cash calls. The CFTC categories cannot identify
a named trader.

## Physical inventory identity

```text
inventory_market_value
  = EIA_commercial_crude_stocks_ex_SPR_thousand_bbl
    × 1,000 × WTI_spot_proxy

annual_SOFR_carry_benchmark
  = inventory_market_value × SOFR_percent / 100
```

This values all reported inventory at one public benchmark and finances all of
it at SOFR only to make the scale legible. It is not the financed share, basis,
borrowing rate, ownership structure, hedge book, or realized carrying cost of
the inventory.

## State rule

Ballast uses a deliberately small vocabulary for commodity/physical channels:

- `CALM`: the worst available channel is below its 80th percentile;
- `TIGHT`: the worst available channel is at or above p80;
- `ACUTE`: the worst available channel is at or above p95;
- `CANNOT_ASSESS`: fewer than half the declared inputs are available, or no
  commodity/physical channel has enough history for a percentile.

SOFR−IORB and CP−Treasury receive separate `NORMAL_RELATIVE`,
`ELEVATED_RELATIVE`, or `TAIL_RELATIVE` overlay labels at the same percentile
lines. They amplify the interpretation of a commodity cash event but never
create one alone. This matters when a mechanically benign level—such as a
zero-basis-point SOFR−IORB spread—ranks high only because most of its history
was negative.

Percentiles use mid-ranks for ties. A flat spread therefore reads p50 rather
than becoming a false p100 event. CFTC channels use up to 260 weekly readings;
daily funding channels use up to 756 observations. At least 20 observations
are required for any percentile, while each contract needs at least 52 aligned
CFTC reports before it is published.

## Point-in-time behavior

The Seiche Time Machine truncates all of these before rerunning the engine:

- public benchmark prices;
- CFTC report dates;
- EIA weekly inventory;
- daily funding rates.

The report date remains the CFTC Tuesday, but the row does not enter a replay
until the normal Friday release date. An EIA Friday period end does not enter
until the normal following-Wednesday Weekly Petroleum Status Report. Both the
observation and assumed availability dates travel in the payload. Holiday-week
exceptions can differ, so this standard-lag treatment is explicitly an
approximation; it is conservative in some holiday weeks and should not be read
as a historical release-calendar archive. A reconstruction is historical-data
replay, not an as-published vintage claim for dates before Seiche began sealing
snapshots.

## Product ownership

| Product | Owns | Does not claim |
|---|---|---|
| **Seiche / Ballast** | Aggregate cash-pressure detection and percentile context | Live depth, executable slippage, named-holder exposure |
| **Undertow** | Position-size exit cost, venue depth, concentration and liquidity withdrawal | Who owns the position |
| **LiquiLens** | Institution exposure from disclosed or consented private-book data and its funding consequences | That an aggregate CFTC category belongs to a named institution |

The `handoffs` block in `seiche.ballast.v1` states these joins in machine-readable
form. The compact `/api/oil-funding` and MCP Oil × Funding view include current
Ballast readings and the chartless Oil Market Structure block (live Cushing
stocks and Brent−WTI basis separated from dated capacity and chokepoint
references), but omit chart history.

## Public sources

- CFTC Disaggregated Commitments of Traders, futures-only (`72hh-3qpy`), weekly
  Tuesday positions normally published Friday.
- WTI and Henry Hub public spot benchmarks (`DCOILWTICO`, `DHHNGSP`).
- EIA US commercial crude stocks excluding SPR (`WCESTUS1`), weekly.
- SOFR, IORB, three-month AA nonfinancial commercial paper and three-month
  Treasury yields (`SOFR`, `IORB`, `DCPN3M`, `DGS3MO`).

## Expansion gate

Gold, copper, grains and refined products should be added only when all of the
following are recorded: exact CFTC contract code, correct multiplier and unit,
stable public price proxy, at least 52 aligned reports, physical-collateral
interpretation, replay test, and a product-specific caveat. Contract count is
not a success metric; correctly bounded cash-pressure coverage is.
