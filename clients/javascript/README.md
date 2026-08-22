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

Use `fetchWorldMarkets({ section: "china_macro" })` for the release-pinned NBS
series catalog and, when present, owner-attested provenance. This projection
contains no NBS values, raw export, history, gauge input, or scoring input.
`knowledge_time` records when the signed export became knowable; it must never
be promoted into `as_of` or `clocks.selected_evidence_as_of`. The client fails
closed if the response claims otherwise, including when China context appears
inside `section: "all"`.

When using custom limits, pass the same `timeoutMs` and `maxResponseBytes` to
`contractReceipt(payload, {...})`; its `client_limits` block is an effective
request receipt, not a claim about library defaults.
