# Rights-reviewed direct-OFR research distribution kit

This directory contains draft metadata projections for the same two audited
direct-OFR CSVs already tracked under `integrations/datacommons`. It does not
copy those observations, mint a DOI, or submit anything to an external service.

Current invariant: **10 series, 11,163 records, two pinned CSVs, direct OFR
only**. The repo-market file has 10,489 records and the money-market-fund file
has 674. FRED-fetched values, runtime caches, New York Fed reference-rate rows,
the mismatched primary-dealer row, licensed/restricted series, and all
Seiche-derived outputs are excluded.

## Validate

```sh
python3 distribution/datasets/stage.py --validate-only
python3 -m pytest -q distribution/datasets/test_distribution_kit.py
```

Validation recomputes file hashes, sizes, row counts, the exact ten-variable
allowlist, duplicate keys, date/value types, the upstream Data Commons build
manifest, rights-review markers, metadata references, notebook hygiene, and
the draft/no-DOI boundary. It is entirely offline.

## Stage by reference

The optional staging action creates symlinks, never CSV copies, and refuses a
non-empty destination:

```sh
python3 distribution/datasets/stage.py --stage /tmp/seiche-research-stage
```

This creates separate `huggingface/` and `kaggle/` layouts with symlinks to the
two reviewed source files and their draft metadata. It does not run an upload
client or authenticate to any service. Inspect staged links and obtain owner
approval before any publication action.

## Metadata projections

- `huggingface/README.md`: dataset card and data-file pattern
- `kaggle/dataset-metadata.json`: Kaggle draft metadata
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
