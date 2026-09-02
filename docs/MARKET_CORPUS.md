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
| `/v1/catalog` | Corpus counts, evidence vocabulary, index identity and distinct clocks |
| `/v1/datasets` | Paginated attested public objects, bound to one immutable index |
| `/v1/datasets/{dataset_id}` | One exact object, rights decision, provenance and permitted structural profile |
| `/v1/bis/flows?product=seiche` | BIS flows mapped to Seiche, including cautions and product fit |
| `/v1/bis/observations?flow_id=...` | Rights-gated observations for one BIS flow |
| `/v1/bis/records?flow_id=...` | Full normalized BIS records with series keys, dimensions, event and knowledge clocks, source attribution, revisions, and snapshot-bound cursor pagination |
| `/v1/bis/flows/{flow_id}/manifest` | Immutable flow-generation receipt and complete content-addressed shard inventory |
| `/v1/bis/flows/{flow_id}/shards/{compressed_sha256}.jsonl.gz` | Resumable gzip shard transfer with digest, ETag, HEAD and byte-range semantics |
| `/v1/bis/revisions?flow_id=...` | Revision history for one BIS flow |
| `/v1/seiche/markets` | Observed Seiche normalized partitions and source families |
| `/v1/seiche/exports` | Export registry; protected generations retain `download: null` |
| `/mcp` | Structured corpus MCP endpoint |

Dataset pagination uses `limit` and the returned opaque `next_cursor`. Every
page and detail carries `release_id`, `index_artifact_id`, `index_sha256` and
verification time. Supported filters are `group`, `data_class`,
`acquisition_review`, `engine`, `collection_kind` and `publication_state`.
A changed index fails explicitly instead of combining generations.

The 10 August cut contains 1,118 acquisition attempts reconciled to 1,110
verified unique objects; all eight failed attempts have a verified successful
retry and none is unresolved. An all-false metadata decision omits the object
identifier entirely and increases only the aggregate withheld count. Eligible
objects may publish an attested structural profile, never inferred sample
values. Acquired BSE/NSE/NY Fed supplemental cuts and nine restricted recipes
are represented as metadata-only collections with their distinct acquisition
review, file/row/date/manifest facts and explicit value/download boundary.

`/v1/bis/records` never scans a monolithic flow artifact on request. The
publisher materializes each immutable normalized generation into independently
compressed, bounded shards. Cursors bind the flow, source generation, manifest
identity, shard and row offset; a changed or missing generation fails closed
instead of silently mixing snapshots. The older `/v1/bis/observations` route
remains a compact state projection for compatibility.

For a complete transfer, fetch the flow manifest once and download its shard
URLs. Each URL contains the shard's compressed SHA-256; responses expose the
same identity through `ETag` and `Digest`, support `HEAD` and a single byte
`Range`, and never redirect to raw storage. Resume only against the same
manifest identity, verify every compressed digest, then verify the manifest's
row and byte totals before admitting the local copy.

## Market Atlas UI

The `MARKET ATLAS` tab at `https://seiche.info/#corpus` joins two deliberately
separate serving planes:

- `GET /api/v2/markets` and
  `GET /api/v2/markets/{market_id}/series?n=200` provide indexed canonical
  Seiche instruments and observations for interactive charts and ledgers.
- The corpus service provides the full bulk registry, normalized BIS records,
  revision history, source receipts and protected export states.

This separation is a latency and evidence boundary. Corpus rows do not become
Seiche analytics merely because the UI can inspect them.

## Non-negotiable boundary

- Preserve acquisition attempts, unique-object identity, `data_class`, evidence
  class, rights, source clocks and knowledge clocks as separate fields.
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
- A publisher source page is provenance. It is not proof that Seiche may
  redistribute the underlying bytes. Private paths, effective download URLs,
  signed query strings and acquisition errors never enter the public index.

If the corpus becomes an input to a Seiche analytic later, materialization must
happen asynchronously under the source pipeline, enter the canonical store,
and pass the existing publication, freshness, evidence, temporal-split and
rights checks before any output can use it.

## UI failure behavior

The MARKET ATLAS tab loads the canonical market catalog, each selected series,
attested evidence objects and their on-demand details, BIS flows and records,
revisions, market partitions and
exports independently. A failed resource is shown as unavailable while
successful resources remain visible. The UI never substitutes empty coverage,
a zero value or a healthy state for a failed request.
