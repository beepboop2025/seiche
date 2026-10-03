# Source coverage and revision clocks

The completed snapshot's provenance includes every entry in `ALL_SERIES`, plus named structured source envelopes. Registry metadata is not permission to export a history: `history_export_allowed` and `history_export_restriction` use the same existing guard as the public JSON and CSV routes.

For a captured series, the source's original `Series.provenance()` remains authoritative. Observation date, fetch time, native cadence and publication policy keep their separate meanings. This includes weekly-publication H.10 data and the distinct ECB and CBUAE FX policies. A recent fetch does not prove that an upstream published a new observation or revised value.

`availability` identifies three states:

- `available`: a registered source identity is present in the completed source snapshot. Its original freshness classification may still be stale or dead.
- `empty`: the source returned a series with no observations. Its actual fetch clock is retained; no observation date is invented.
- `unavailable`: no matching admitted series is present, or its identity is mismatched or ambiguous. Observation and fetch clocks are null, with `staleness: unknown` and a specific `unavailable_reason`.

The original redistribution filter runs first. Restricted CFETS rows and undeclared aliases cannot expand this registry. Provenance contains metadata, not source values or history. Existing history-export restrictions stay in effect, including the dataset-specific distinction between permitted ECB FX reference data and the separately restricted ECB rate history.

Structured source envelopes can contain heterogeneous dated tables, statements or events. Their retrieval clock remains visible, but their observation freshness stays unknown. Missing envelopes remain explicit. Consumers should count the registry rows and structured envelopes separately rather than treat every row as an independently classified economic series.

The source worker polls every five minutes while each collector honors its own TTL. `collection_ttl_minutes` reports that acquisition policy; it is not the observation frequency or a guaranteed public-snapshot refresh deadline. GDP is checked daily for revisions to existing quarterly dates. The original full-history collector appends the new knowledge vintage and retains the old one. A failed refresh preserves the old values and fetch clock.

GDP's raw `asof` is the quarter's start, not its publication date. The existing observation-age classification and grace remain unchanged. `observation_date_semantics: quarter_start`, a null `official_publication_at`, and `publication_freshness: unknown` make that limit explicit: an old quarter start can be the latest published quarter, while a recent fetch can contain a revised value for that same quarter. Establishing latest-vintage freshness requires independent release/vintage evidence that the current CSV collector does not capture.

`SRF_CEILING` uses the existing hourly funding acquisition interval. A daily official row can advance with an unchanged rate; the collector retains that observation date without implying a rate change. This replaces the twelve-hour cache delay observed for `DFEDTARU`. Other source TTLs are unchanged.

REST health, MCP `data_health` and a newly produced static snapshot receive the same completed provenance. An older published static snapshot does not become fresh merely because the API has advanced; publication still needs its own served-byte and semantic readback.
