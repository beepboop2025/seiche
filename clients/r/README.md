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

`fetch_world_markets("china_macro")` exposes the release-pinned NBS series
catalog and, when available, owner-attested provenance. It is metadata-only:
no NBS value, raw export, history, gauge input, or scoring input is accepted by
the client. Keep `knowledge_time` separate from the null `as_of` and
`clocks$selected_evidence_as_of` fields; it is the time the signed export became
knowable, not a China economic observation date. The same validation applies
when China context is included by `section = "all"`.

If a call overrides `timeout_seconds` or `max_response_bytes`, pass those same
values to `contract_receipt()`. The receipt then records the effective limits
used for that observation.
