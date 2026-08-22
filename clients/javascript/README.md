# Seiche public REST example for JavaScript

`world-markets.mjs` uses the built-in `fetch` available in Node 18 and later.
It calls the anonymous world-markets endpoint and validates the same schema,
selection, clock, citation, and partial-coverage boundaries as the Python and R
examples.

```sh
node clients/javascript/world-markets.mjs
```

The example permits at most 15 seconds and 2 MB per response, with no automatic
retry. It does not use a token. A service 503 remains unavailable evidence.
Keep `clocks.snapshot_generated_at`, `clocks.evaluation_at`, and every evidence
as-of field distinct. The response is bounded research context, not exhaustive
market data or investment advice.

When using custom limits, pass the same `timeoutMs` and `maxResponseBytes` to
`contractReceipt(payload, {...})`; its `client_limits` block is an effective
request receipt, not a claim about library defaults.
