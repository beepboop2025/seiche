# GIFT City, India–UAE and gold funding desk

Start with the public [/gift-city/ workflow guide](https://seiche.info/gift-city/),
or open `/#gift%20city` for dated USD/INR funding, CBUAE VAT FX references,
ECB currency comparisons, COMEX gold positioning and a gold inventory financing
scenario. This is a public research workflow; it does not determine venue,
import, regulatory or collateral eligibility.

## What changed

- RBI collection checks hourly while its underlying observations remain daily.
  Every daily market becomes aging after one missed expected publication and
  stale after two. RBI money-market operations report the previous business
  day; holidays and the distinction between inferred publication clocks and
  observed source timestamps remain explicit.
- Seven CBUAE reference currencies are acquired through the ordinary source
  worker. Original bytes, source identity, observation date, collection time and
  atomic capture metadata accompany the data. The USD/AED, AED/INR and USD/INR
  views never mix CBUAE and ECB legs.
- CFTC disaggregated futures-only collection also admits COMEX gold code 088691.
  Open interest, managed-money net and producer net retain their weekly report
  date. The nominal Friday publication schedule is not a publication receipt.
  This does not add a gold price or enter gold into Seiche's stress composite.
- The gold calculator accepts explicit prices and costs. It uses Decimal,
  caller-selected ACT/360 or ACT/365, and 31.1034768 grams per troy ounce.
  Gross kilograms times fineness gives fine grams. Fees are added separately;
  simple interest applies to the metal value. The output is INR per fine gram.

## API and agent tools

```sh
curl https://api.seiche.info/api/v2/gift-city
curl https://api.seiche.info/api/v2/gift-city/gold-carry \
  -H 'Content-Type: application/json' \
  -d '{"quantity_kg":"1","fineness":"0.995","price_usd_per_oz":"4000","annual_rate_pct":"6","days":30,"day_count":360,"fx_inr_per_usd":"90","fees_usd":"10"}'
```

The second command uses illustrative caller assumptions, not current quotes.
The corresponding public MCP tools are `gift_city_context` (no arguments) and
`gold_inventory_carry` (the same scenario object). Prices, rates and FX are
decimal strings; days and day count are integers. Scenario inputs are not
persisted, and HTTP results use `Cache-Control: no-store`. The public evidence
endpoint performs bounded store reads; collection stays in scheduled workers.

These interfaces become publicly available only after the candidate application
and matching frontend are released through the existing production gates.

## Practical review sequence

1. Identify the actual gold instrument, fineness, location and delivery terms.
2. Record purchase funding, margin, fees, settlement deadlines and currency.
3. Inspect source dates and missing releases in the funding and FX panels.
4. Enter the institution's own dated price, facility rate, FX and costs.
5. Export the scenario with the evidence snapshot used for the review.
6. Use Undertow for a separate sale-proceeds and settlement-availability review.

## Data boundaries

CBUAE references are published for VAT valuation. They are not executable dealer
quotes. ECB references likewise cannot supply a hedge or an intraday spread.
See [the CBUAE source contract](CBUAE_FX_DATA.md) and
[ECB collection and rights](ECB_FX_DATA.md).

DONIA remains unavailable until its own recurring numerical source is admitted.
Its mixed secured/unsecured AED overnight definition is different from SOFR;
the AED/USD peg is not an AED funding rate. IIBX/LBMA/dealer trading prices and
venue depth require the appropriate data rights. The desk supplies no synthetic
substitute for them. CFTC positions are not IIBX inventory.

The live public source check on 2 October 2026 retrieved CBUAE and ECB references
dated 2 October. The normal CFTC collector crossed the Friday release boundary
and retrieved gold positions dated 29 September at 19:31 UTC. These are
development verification observations, not a promise
about the current live deployment or future source availability.

Official references: [IFSCA metals circulars](https://www.ifsca.gov.in/Pages/Contents/Metals%20and%20Commodities),
[IIBX](https://www.iibx.co.in/),
[CBUAE rates](https://centralbank.ae/en/forex-eibor/exchange-rates/),
[CBUAE DONIA term sheet](https://centralbank.ae/media/kuqd0q5o/attachment-7_donia-term-sheet.pdf),
[CFTC](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm).
