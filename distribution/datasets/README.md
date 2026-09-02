# Rights-reviewed direct-OFR research distribution kit

This directory contains catalog-planning metadata plus publication-ready
Hugging Face and Kaggle projections for the same two audited direct-OFR CSVs
already tracked under `integrations/datacommons`. It does not copy those
observations, mint a DOI, or claim an external listing without a receipt.

Current invariant: **10 series, 11,163 records, two pinned CSVs, direct OFR
only**. The repo-market file has 10,489 records and the money-market-fund file
has 674. FRED-fetched values, runtime caches, New York Fed reference-rate rows,
the mismatched primary-dealer row, licensed/restricted series, and all
Seiche-derived outputs are excluded.

The metadata publication and source-data revision are deliberately separate:
catalog citations resolve to the versioned `v0.12.0/distribution/datasets`
tree, while every observation URL and hash stays pinned to the audited
`93e83bbc.../integrations/datacommons` source tree.

## Validate

```sh
python3 distribution/datasets/stage.py --validate-only
python3 -m pytest -q distribution/datasets/test_distribution_kit.py
```

Validation recomputes file hashes, sizes, row counts, the exact ten-variable
allowlist, duplicate keys, date/value types, the upstream Data Commons build
manifest, rights-review markers, metadata references, notebook hygiene, and
the catalog draft/no-DOI boundary, and the absence of stale "not submitted"
copy in platform upload payloads. It is entirely offline.

## Stage by reference

The optional staging action creates symlinks, never CSV copies, and refuses a
non-empty destination:

```sh
python3 distribution/datasets/stage.py --stage /tmp/seiche-research-stage
```

This creates separate `huggingface/` and `kaggle/` layouts with symlinks to the
two reviewed source files and publication-ready platform metadata. It does not
run an upload client or authenticate to any service. External status remains in
`distribution/submissions.csv`, never inferred from a local staging directory.

## Publish and receipt

Authenticate without printing tokens, stage into a new empty temporary
directory, and pin the Hugging Face and Kaggle client versions whose commands
and native metadata checks were release-tested:

```sh
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/seiche-research-stage.XXXXXX")"
python3 distribution/datasets/stage.py --stage "$STAGE_DIR"
uvx --from huggingface_hub==1.28.0 hf auth whoami
uvx --from huggingface_hub==1.28.0 hf repos create \
  seiche-info/seiche-audited-direct-ofr-time-series \
  --repo-type dataset --public --exist-ok
uvx --from huggingface_hub==1.28.0 hf upload \
  seiche-info/seiche-audited-direct-ofr-time-series \
  "$STAGE_DIR/huggingface" . --repo-type dataset \
  --commit-message "Publish audited direct-OFR snapshot 0.1.0"

uvx --from kaggle==2.2.4 kaggle auth login
uvx --from kaggle==2.2.4 kaggle datasets create -p "$STAGE_DIR/kaggle" \
  --public --quiet --keep-tabular --dir-mode skip
```

For Hugging Face, verify the repository plus Dataset Viewer `/is-valid`,
`/splits`, `/first-rows`, `/size`, and `/parquet` responses. For Kaggle, wait
for processing, then verify the public page, exact two-file inventory, row
counts, and downloadable hashes. Only then change the corresponding ledger row
to `listed` and record immutable/public URLs plus the upload receipt.

## Metadata projections

- `huggingface/README.md`: publication-ready dataset card and data-file pattern
- `kaggle/dataset-metadata.json`: publication-ready Kaggle metadata
- `croissant.json`: MLCommons Croissant 1.0 JSON-LD
- `datapackage.json`: Frictionless Data Package descriptor
- `dcat.jsonld`: DCAT 3 catalog, dataset, and distributions
- `ro-crate-metadata.json`: RO-Crate 1.3 research object
- `datacite-draft.json`: DataCite 4.x planning record without a DOI

Each projection preserves the OFR credit request and links to the
[OFR API](https://www.financialresearch.gov/short-term-funding-monitor/api/),
[legal notice](https://www.financialresearch.gov/legal-notices/),
[repo release](https://www.financialresearch.gov/short-term-funding-monitor/datasets/repo/),
and [MMF release](https://www.financialresearch.gov/short-term-funding-monitor/datasets/mmf/).
This is an engineering rights review, not legal advice or publisher endorsement.
