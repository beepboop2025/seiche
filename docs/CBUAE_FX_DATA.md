# CBUAE VAT reference FX admission

Verified 2026-10-02 UTC. This adapter supports AED context for the India–UAE
research corridor. It does not provide tradable quotes, forwards, a gold price,
settlement eligibility or a tax determination.

## Source and reuse basis

- [CBUAE exchange-rate dataset](https://centralbank.ae/en/forex-eibor/exchange-rates/)
  describes daily exchange rates against AED for VAT-related obligations.
- The page's [public all-currency table](https://centralbank.ae/umbraco/Surface/Exchange/GetExchangeRateAllCurrency)
  returns an HTML fragment with currency names, rates and a dated `Last updated`
  field. No credential, private interface or authenticated account is used.
- [CBUAE Open Data Policy](https://centralbank.ae/en/open-data-landing/open-data-policy/)
  permits use and reuse of open data and files on its data pages, with source
  attribution and a direct link to the original information. It excludes other
  intellectual property, including trademarks. Admission here is limited to the
  numerical reference table under that policy; it is not a blanket licence to
  republish the website, logos, commercial third-party feeds or exchange data.
- [General site terms](https://centralbank.ae/en/footer/terms-and-conditions/)
  apply to other website material. Retain the copyright attribution, do not
  imply endorsement, and reassess if the dataset-specific policy changes.

Display/export attribution: **Source: Central Bank of the UAE. © Central Bank of
the UAE.** Link the dataset and policy beside the values. Clearly label any
calculated inversion or cross-rate as Seiche's calculation from reference data.

## Units, clocks and refresh

`CBUAEFX_INR = 0.038126` means **0.038126 AED for one INR**. Thus
`1 / CBUAEFX_INR` yields INR per AED; `CBUAEFX_USD / CBUAEFX_INR` yields INR per
USD. Never concatenate this series with ECB `currency/EUR` series or use it as
an executable USD/INR quote. Keep both legs from the same dated generation.

The observation day comes from the publisher table's `Last updated` field,
not from the fetch clock. This is expressly recorded as
`observation_date_basis=publisher_table_last_updated`. The displayed timestamp
has no zone marker; `publisher_updated_at` uses Asia/Dubai based on publisher
locality and records that assumption. `source_publication_time` remains null:
the table does not establish a market fixing or original publication timestamp.
The verification fetch on 2026-10-02 returned a table updated at
`Friday 02 October 2026 06:05:15 PM`; this is evidence of that response, not a
guarantee of a daily 18:05 publication schedule.

The collector has a six-hour network TTL; the source describes daily updates.
TTL suppresses redundant downloads and does not declare data fresh. Consumers
must separately evaluate the source observation date. This adapter makes no
unsupported holiday/publication-time guarantee. Missing optional currencies
are absent; failed refreshes retain the prior clocks and emit a source fault.

The parser validates the complete table, including unselected rows. Scientific
notation is valid (the live table contains `2E-06`); nonfinite/zero/negative
values, duplicate currencies, malformed/truncated tables, ambiguous dates and
future timestamps are rejected before writes. USD and INR are required; EUR,
GBP, JPY, CHF and SGD are returned when present.

## Storage and integration

`await cbuae_fx.fetch(client, faults, force=False)` returns `dict[str, Series]`,
with mnemonics `CBUAEFX_<ISO>`, source `cbuae_fx`, remote identifiers
`VAT/AED_PER_<ISO>`, units `AED/<ISO>`, and daily frequency. The optional
`raw_root` argument is available for isolated verification.

Raw HTML is hash-addressed; a capture manifest records the dataset and policy
URLs, attribution, source/fetch clocks, orientation and exact latest values.
`cbuae_fx:latest` and the individual currency series commit atomically using
the existing store compare-and-swap guard. Earlier observations and same-day
vintages remain archived. The immutable manifest is checked before cache use;
an older concurrent fetch cannot replace a newer generation.

No network call should run on an API request path. Register the collector in
the scheduled assembly flow and expose only stored, validated generations.
Source failures should remain visible in data health and corridor responses.

`cbuae_fx.read_references(now=aware_datetime)` reads the validated local
generation and returns USD/AED, AED/INR and USD/INR with explicit observed or
derived status. It never mixes CBUAE and ECB legs. Its conservative age policy
labels the same Dubai calendar day fresh, one day aging, and older values
stale; this is disclosed as an age heuristic rather than a verified release
calendar. Missing/invalid generations return three explicit unavailable rows.
Public capture metadata excludes the archive's private filesystem path.

## Related sources kept separate

[DONIA methodology](https://centralbank.ae/media/kuqd0q5o/attachment-7_donia-term-sheet.pdf)
defines a mixed secured/unsecured AED money-market index, not a USD peg or an
FX quote. Its transactions and publication cadence require their own adapter.
The daily CBUAE liquidity workbook also contains M-Bill sections attributed to
Bloomberg: this FX admission does not confer redistribution rights over those
sections. IIBX/India INX/NSE IX and other exchange prices require their own
market-data rights review and must not be presented as part of this feed.
