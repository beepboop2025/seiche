# Structured money-market, forex and China research

The WORKBENCH tab brings three research tasks into one workspace: inspect
funding observations, compare dated currency reference rates, and examine the
China economic evidence accepted from Palimpsest. It is an additive research
surface. Its calculations do not alter the funding gauge or authorize trades.

## Data available in this implementation

| Dataset | Structured coverage | Observation and history contract |
| --- | --- | --- |
| NY Fed overnight benchmarks | SOFR, TGCR, BGCR, EFFR and OBFR: P01, P25, median, P75, P99 and volume; four SOFR average/index series | 34 instruments, including 23 newly retained series; native publication clocks, revisions and normalized units |
| Federal Reserve H.10 | 22 existing currency references plus USD as the calculation anchor | Current cached vintage; USD/local conventions explicitly normalized |
| ECB FX references | 29 current quote currencies plus EUR as the calculation anchor | Full official history bootstrap, rolling 90-day updates, monthly full refresh; retired currencies retained in storage but excluded from current coverage |
| Palimpsest China WDI export | All current series in an independently accepted export, plus selected annual history | Offline owner signature, source rights, exact export identities, release/collection/acceptance clocks and withdrawal filtering required |

Funding embeds the existing canonical explorer, including native units,
observation evidence, source status, charting, pagination and export. The NY Fed
depth is populated by the existing scheduled adapters after deployment; adding
the registry entries does not create historical observations retroactively.

## API and MCP

REST and the public `market_workbench` MCP tool share
`seiche.market-workbench.v1`:

```text
GET /api/v2/market-workbench?provider=ecb&base=USD&quote=CNY&days=3650
GET /api/v2/market-workbench?provider=h10&base=EUR&quote=JPY&days=365
```

```json
{
  "name": "market_workbench",
  "arguments": {
    "provider": "ecb",
    "base": "USD",
    "quote": "CNY",
    "days": 365,
    "china_series": "cn.wdi.broad_money_growth"
  }
}
```

`china_series` is optional and must exist in the accepted export when that
export is available. `days` is an inclusive calendar-date lookback bounded to
30–3650 days. Responses contain the selected provider's comparison rows, one
selected pair history, source identities, observation dates, native quote
conventions, capture clocks, age states, and China structural evidence. Neither
REST nor MCP starts a source download or model fit during the request.

## Currency calculations

Every displayed value means **quote currency units per one base currency**.
Positive percentage change means the quote currency weakened against the base.
H.10's EUR, GBP, AUD and NZD fields use the opposite native convention from the
other H.10 fields; their direction is declared explicitly.

Crosses use only the exact intersection of source observation dates. Providers
are never mixed, and neither leg is forward-filled. For example, the ECB
USD/CNY cross is `(CNY per EUR) / (USD per EUR)` on the same source date. A missing
leg leaves the cross unavailable. Direct publisher quotes are `observed`;
inversions and crosses are `derived`.

Changes count 1, 5, 20 or 60 observed intervals, not calendar days. Realized
volatility is the population standard deviation of 20 log returns, annualized
with 252 observations. It is unavailable if any interval spans more than seven
calendar days. The current cached vintage must not be used as a publication-time
backtest. A recent download does not refresh an old observation date.

ECB age labels allow three calendar days for `fresh`, six for `aging`, eighteen
for `stale`, then `dead`. H.10 allows seven, fourteen and forty-two days because
its daily observations are delivered on a weekly publication cycle. These are
reference-data age labels, not confirmation of an executable or open market.

## China interpretation and remaining activation

Four channels organize the accepted observations: domestic money and credit,
external balance, reserves, and activity. Their explanations describe the
economic mechanism and its limits. Annual WDI rows do not measure current repo
stress, dealer order flow or daily intervention. The selected CNY reference has
its own daily clock; it is not CNH or the PBOC central parity fixing.

The existing production Palimpsest owner key allowlist was empty when this
feature was prepared on 9 September 2026. Production recovery evidence also
showed no active China economic bundle. Consequently the interface must show
the numerical economic panel as unavailable until activation is completed.
Tests with genuine signed **test** bundles prove the integration; they do not
establish production acceptance.

Follow [the acceptance contract](PALIMPSEST_CHINA_ECONOMIC_ACCEPTANCE.md): create
a fresh producer bundle with exact current source and attestation evidence,
prepare the review claim, sign it on the dedicated offline signer, and pin only
its public key through a signed Seiche release. The contract forbids copying
the private key to the claim or application host. The old activation launcher
targets systemd/Hetzner; Railway activation needs an equivalent reviewed
durability and publication path. Do not wire the eleven files manually around
that activation contract or treat public catalog metadata as accepted values.

The current Palimpsest public monthly NBS panels and its separately reviewed
Seiche WDI export have different publication contracts. Their numerical rights
and acceptance are not interchangeable.

## Coverage relative to commercial terminals

Bloomberg and LSEG include licensed contributor, dealer and broker data that
this public reference dataset does not supply. Outstanding gaps include
executable bid/ask and depth, intraday ticks, tenor-specific forward and swap
curves, NDFs, CNH and the CNY/CNH basis, security-level certificates of deposit
and commercial paper, and entitled Chinese benchmarks. Filling these requires
the relevant provider agreement and field-level display/redistribution rights.
No such feed is simulated or advertised as installed here.

Source-specific documentation: [NY Fed depth](NYFED_DISTRIBUTION_DEPTH.md) and
[ECB collection, rights and refresh](ECB_FX_DATA.md).

## Validation and release

The initial isolated ECB probe retained 220,600 observations across 41 historical
currencies from 1999-01-04 through 2026-09-08. A subsequent rolling refresh
preserved that history. Those are dated development-proof counts, not production
coverage claims. The ten-year workbench view returned 2,557 dated USD/CNY rows
and 29 available pairs. NY Fed's separate 45-day probe returned 1,024 observations
across 34 instruments.

The source sweep collects ECB FX through the existing scheduled ingestion
runtime. One transaction commits every currency, its vintage records and the
capture manifest. Parsing/rights/date failures retain explicit unavailability
or the original stale cache; they do not create zero rates or advance clocks.

Publish this runtime change using a new release version and the existing
exact-SHA Railway application/recovery procedure. Preserve the active controller
and immutable predecessor receipts. Verify the new REST/MCP response, ECB capture
manifest and canonical NY Fed additions after the first successful source sweep.
Do not describe a local capture, branch, package or UI build as a live deployment.
