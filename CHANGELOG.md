# Changelog

<!-- markdownlint-disable MD024 -- Changelog sections intentionally repeat. -->

All notable changes to Seiche are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Generated dispatches and routine market-data refreshes are not listed unless
they change a public contract, methodology, or release artifact.

## [Unreleased]

## [0.12.0] - 2026-09-02

### Added

- Added the Market Atlas and its structured, rights-aware public corpus with
  bounded snapshot cursors, explicit evidence states, and MCP exploration.
- Added contextual share routes and content-addressed cards so a shared market
  view retains its own evidence context instead of collapsing to the homepage.
- Added exact snapshot hydration, a prebuilt deep cache, and Palimpsest China
  evidence-lake intake with explicit provenance and activation boundaries.
- Added an external watchdog for LiquiLens runner-maintenance debt, including
  source-bound status ingestion, deadline escalation, and recovery proof.
- Added a deterministic `seiche.risk-context.v1` REST/MCP projection for Trade
  Safety integrations. It reads only a completed cache, repeats rights and
  clock validation, and carries regime, index, coverage, staleness counts, and
  conservative snapshot/evidence clocks.
- Added the private Agent Room preview: five bearer-identity REST/MCP
  capabilities, Ed25519 client signatures, server co-signatures, immutable
  membership, optimistic sequence control, nonce replay defense, bounded
  rights-aware evidence metadata, and a verified per-room hash chain.

### Changed

- Bound Railway release gates, snapshot prebuilds, runtime roots, deployment
  logs, and recovery handoffs to exact source and OIDC-attested receipts.
- Bounded production snapshot refresh cost and strengthened market-corpus
  readability, publication receipts, and public discovery contracts.
- Advanced the canonical application, package, MCP, OpenAPI, citation, and
  scientific metadata identity together to `0.12.0`; the independent market
  corpus remains version `1.0.0`.

### Security

- Added root-sealed release, recovery, and watchdog receipts with exact-SHA
  admission, transactional installation, and fail-closed authority checks.
- Kept the Trade Safety projection metadata-only, derived, non-executable, and
  ineligible for real-money use. It performs no request-time collection,
  fitting, network, notary, or broker work. It does not inspect the attestation
  ledger or treat a separately verified stream attestation as per-order
  execution authority.
- Kept Agent Room permanently outside order, execution, payment, settlement,
  and custody authority. Anonymous and x402 callers cannot discover it; actor
  identity comes only from a verified bearer, payloads reject secret-shaped and
  executable fields, and the dedicated owner-only SQLite database is captured
  through online backup and isolated restore verification.
- Kept protected exports, restricted sources, incomplete evidence, and
  cross-product health claims outside publication authority.

## [0.11.1] - 2026-08-24

### Fixed

- Restored the literal `mcp-name: io.github.beepboop2025/seiche` ownership
  marker in the PyPI long description and added a release regression contract
  so the official MCP Registry can validate the package namespace.
- Synchronized the hosted runtime, Python package, MCP server card, AI catalog,
  and citation metadata on the superseding `0.11.1` patch identity. The
  immutable `0.11.0` artifacts remain available as historical receipts.
- Made both static publishers fail closed before their first public write until
  the pinned signed tag, exact wheel and source-archive bytes, fault-free hosted
  runtime, and matching MCP discovery record all exist.
- Published the complete anonymous MCP inventory in the AI catalog: eleven
  tools, four prompt templates, and an explicit zero-resource boundary.

## [0.11.0] - 2026-08-22

### Added

- Repository security, contribution, conduct, and maintainer-governance policies.
- Structured issue forms, a pull-request checklist, CODEOWNERS, and Dependabot
  configuration for Python, npm, and GitHub Actions dependencies.
- OpenBB provider/router packaging; hosted MCP client configurations; and
  copy-paste Python, R, and JavaScript world-markets clients.
- A signed-release/OIDC OpenBB publication workflow, exact provider artifact
  verifier, clean wheel/sdist smoke gates, and official listing packet.
- A commit-pinned research notebook and rights-reviewed direct-OFR distribution
  kit with Hugging Face, Kaggle, Croissant, Frictionless, DCAT 3, RO-Crate 1.3,
  and DOI-free DataCite metadata.
- Distroless multi-platform container packaging, Compose hardening, GHCR
  provenance/SBOM publication, citation metadata, and a receipt-backed external
  submission ledger.
- A same-origin MCPub compatibility document at `/.well-known/mcp.json`, ready
  for receipt-gated directory submission after the release reaches production.

### Changed

- Expanded continuous official-source ingestion, readiness evidence, backup and
  restore verification, deploy handoff checks, and source-worker supervision.
- Advanced the NY Fed backfill generation so existing installations collect
  full SOFRAI averages/index history once, without reinterpreting prior markers.
- Published accurate live MCP-directory ownership/freshness records and kept
  external OpenBB, dataset, catalog, OpenAI, and DOI claims receipt-gated.
- Bound Python, MCP, AI-catalog, citation, container, and scientific metadata to
  one `0.11.0` release identity.
- Moved Python packaging to a pinned, reproducible Hatchling backend and made
  CI compare independently timestamp-perturbed wheel and source builds before
  PyPI publication; exact artifact allowlists, wheel RECORD validation, and
  PEP 639 AGPL-file verification run again on immutable PyPI bytes.
- Migrated `openbb-seiche` to standardized PEP 621/639 metadata with a pinned
  Poetry Core backend, reproducible independent-tree builds, and exact PyPI
  inventory reconciliation under per-version publication concurrency.
- Required every signed-tag publisher to prove the release commit is on `main`,
  carried build-once multi-platform OCI bytes through source-free scan and GHCR
  publication jobs, and added pinned native Kaggle metadata/inventory validation
  alongside the Hugging Face schema gate.
- Made the canonical direct-OFR DCAT URLs deployable, added pinned native
  research-metadata and Zenodo-schema gates, and normalized machine-readable
  media and license identifiers.
- Kept scheduled dispatches on the static-publish path and taught the production
  poller to treat only exact desk-authored, content-only commits as non-release
  updates; mixed or code changes still require the signed release path.
- Made least-privilege market backups compatible with a service that lacks
  `CAP_CHOWN`, while retaining backup freshness and restore-receipt gates.

### Security

- Pinned third-party GitHub Actions to immutable commit SHAs.
- Declared least-privilege read permissions for workflows that previously
  relied on the repository's default token policy.
- Replaced request-derived dispatch paths with enumerated regular-file lookup,
  sanitized attestation storage failures, and bounded public history reads.
- Removed generated passwords and bearer tokens from CLI output in favor of
  atomic, non-overwriting, mode-`0600` credential handoff files.
- Required the pinned release author plus SSH-signed commit and annotated tag
  before PyPI, GHCR, or MCP publication receives authority.
- Replaced self-asserted attestation keys with a release-pinned trust set,
  rejected orphan/duplicate/mismatched evidence, published both OTS proof
  fragments, and reserved "Bitcoin confirmed" for canonical Core-header checks.
- Split package build, pristine-source verification, executable smoke, and OIDC
  publication across isolated runners; bound every publisher to an external SSH
  fingerprint, removed persisted checkout credentials, and kept source checkouts
  out of MCP/container attestation authority domains.

## [0.10.1] - 2026-08-22

### Changed

- Synchronized package, hosted runtime, MCP Registry, AI catalog, publishing
  documentation, and version-contract tests on the `0.10.1 estuary` identity.
- Kept the PyPI stdio package and the eleven-tool public MCP surface on the same
  immutable version.

## [0.10.0] - 2026-08-22

### Added

- Deep public money-market workspaces and AI-citable money, foreign-exchange,
  capital-market, oil-funding, and FX-materials context.
- Versioned world-market evidence contracts with canonical citations, source
  clocks, rights status, and explicit limitations.
- Official MCP Registry and cross-product discovery metadata for the public
  Seiche tool surface.

### Changed

- Expanded publication, editorial, and investigation surfaces while preserving
  deterministic evidence and training boundaries.
- Strengthened ingestion, collector, shared-host admission, backup, restart,
  payment, and release-controller behavior.

### Security

- Added signed-release policy, trusted-main host release control, release-atomic
  activation, sandbox hardening, and private Telegram subscription handling.

## [0.9.1] - 2026-08-08

### Changed

- Published the construction-point-in-time evidence boundary and aligned public
  MCP/catalog descriptions with the shipped surface.

[Unreleased]: https://github.com/beepboop2025/seiche/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/beepboop2025/seiche/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/beepboop2025/seiche/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/beepboop2025/seiche/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/beepboop2025/seiche/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/beepboop2025/seiche/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/beepboop2025/seiche/releases/tag/v0.9.1
