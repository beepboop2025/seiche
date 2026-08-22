# Seiche public REST example for Python

`world_markets.py` uses only the Python standard library and calls the
anonymous `https://api.seiche.info/api/v2/world-markets` contract. It returns
the service response unchanged; the optional receipt projection merely makes
the server's separate clocks, citation, scope, and local safety limits easy to
inspect.

```sh
python3 clients/python/world_markets.py
```

The example has a 15-second timeout, a 2 MB response ceiling, and zero
automatic retries. HTTP 503 remains an unavailable observation, not an empty
or calm result. `generated_at`, `clocks.evaluation_at`, and the domain/source
as-of values have different meanings; do not replace them with the local wall
clock. The response is bounded research context, not exhaustive market data or
investment advice. No API key is used.

`fetch_world_markets("china_macro")` returns the release-pinned NBS series
catalog and, when present, owner-attested provenance. It is deliberately
metadata-only: `values_published`, `raw_evidence_included`,
`history_included`, `scoring_eligible`, and `cn_cny_gauge_eligible` must all
remain false. `knowledge_time` says when the signed export became knowable; it
is not a source observation date, so the selector requires `as_of` and
`clocks.selected_evidence_as_of` to remain null. The example client rejects a
response that crosses any of those boundaries.

If you override either safety limit, pass the same values to
`contract_receipt(payload, timeout_seconds=..., max_response_bytes=...)`. The
receipt records the effective settings rather than silently reporting defaults.
