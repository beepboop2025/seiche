# Seiche public REST example for R

`world_markets.R` uses base R networking plus one small JSON parser,
[`jsonlite`](https://cran.r-project.org/package=jsonlite). It calls the same
anonymous endpoint and validates the same contract boundaries as the Python
and JavaScript examples.

```r
install.packages("jsonlite")
source("clients/r/world_markets.R")
receipt <- contract_receipt(fetch_world_markets("sources"))
str(receipt)
```

The example permits 15 seconds and 2 MB, performs no automatic retry, and uses
no API token. Preserve snapshot, evaluation, domain, and source clocks exactly
as returned. HTTP failures are failures, not empty observations. The response
is bounded research context, not exhaustive market data or investment advice.

If a call overrides `timeout_seconds` or `max_response_bytes`, pass those same
values to `contract_receipt()`. The receipt then records the effective limits
used for that observation.
