# Structured market corpus

Seiche exposes the shared money- and capital-market corpus as a distinct,
read-only discovery service. The canonical root is:

```text
https://api.seiche.info/api/v2/corpus
```

The Seiche UI reads this service directly. No Seiche request handler performs
HTTP self-ingestion, and catalog rows are never merged into board analytics,
signals, forecasts, scores or execution paths.

## Public routes

| Route | Meaning |
|---|---|
| `/healthz` | Service and evidence-root health; use `?deep=true` for deep checks |
| `/v1/catalog` | Counts, byte totals, evidence vocabulary and distinct clocks |
| `/v1/datasets` | Paginated acquisition receipts with rights and split policy |
| `/v1/datasets/{dataset_id}` | One exact dataset receipt |
| `/v1/bis/flows?product=seiche` | BIS flows mapped to Seiche, including cautions and product fit |
| `/v1/bis/observations?flow_id=...` | Rights-gated observations for one BIS flow |
| `/v1/bis/revisions?flow_id=...` | Revision history for one BIS flow |
| `/v1/seiche/markets` | Observed Seiche normalized partitions and source families |
| `/v1/seiche/exports` | Export registry; protected generations retain `download: null` |
| `/mcp` | Structured corpus MCP endpoint |

Dataset pagination uses `limit` and the returned `next_cursor`. Supported
dataset filters are `group`, `data_class`, `license_review`, and `engine`.

## Non-negotiable boundary

- Preserve `status`, `license_review`, `data_class`, evidence class, rights,
  source clocks and knowledge clocks as separate fields.
- `download: null`, `restricted`, `unavailable` and stale states must remain
  explicit. They must never be converted to a zero, a neutral value or a
  healthy state.
- `train_candidate` is not training approval. `evaluation_only`,
  `research_only`, `context_feature`, `outcome_label` and `entity_reference`
  likewise do not grant model, feature, scoring, execution or redistribution
  permission.
- The registered Seiche export is protected until an explicit reviewed public
  export grant exists. Neither this UI nor an API client should synthesize a
  download URL.
- Upstream `url` and `source_page` fields are provenance. They are not proof
  that Seiche may redistribute the underlying bytes.

If the corpus becomes an input to a Seiche analytic later, materialization must
happen asynchronously under the source pipeline, enter the canonical store,
and pass the existing publication, freshness, evidence, temporal-split and
rights checks before any output can use it.

## UI failure behavior

The CORPUS tab loads catalog, receipt, BIS-flow, market-partition and export
resources independently. A failed resource is shown as unavailable while
successful resources remain visible. The UI never substitutes empty coverage
or a healthy state for a failed request.
