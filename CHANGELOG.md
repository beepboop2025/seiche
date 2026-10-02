# Changelog

<!-- markdownlint-disable MD024 -- Changelog sections intentionally repeat. -->

All notable changes to Seiche are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Generated dispatches and routine market-data refreshes are not listed unless
they change a public contract, methodology, or release artifact.

## [Unreleased]

### 0.14.0 release candidate

- Add a GIFT City treasury desk with INR/USD funding, source-separated CBUAE
  VAT-purpose and ECB reference FX, and dated COMEX gold CFTC positioning.
- Add exact-decimal gold inventory carrying-cost scenarios, JSON evidence exports,
  REST routes and the public `gift_city_context` / `gold_inventory_carry` MCP tools.
- Poll RBI hourly while preserving daily observations; apply its previous-business-day
  reporting convention and distinguish fresh, one-release aging and stale daily evidence.
- Admit only the reviewed CBUAE dataset with attribution, source-clock checks, atomic
  raw capture and provenance. AED DONIA and executable bullion quotes remain explicit gaps.
- Bind candidate publication metadata to new corpus receipt `r27`; corpus identity,
  data hashes and original evidence clocks remain unchanged.

## [0.13.16] - 2026-10-02

### Fixed

- Refresh weekly Federal Reserve H.4.1 inputs when the scheduled release crosses
  a cached fetch, instead of retaining the previous observation for twelve hours.
  Retry a delayed FRED mirror hourly while preserving source and fetch clocks.
- Evaluate H.4.1 freshness against Thursday publication, including federal
  holidays and New York daylight saving time, across short and long history aliases.
- Describe H.10 FX exports as daily observations released weekly, consistently
  with the existing source freshness policy.

### Changed

- Bind this application source to corpus receipt `r26`; independent corpus data,
  versions, rights and original evidence clocks remain unchanged.

## [0.13.15] - 2026-10-01

### Fixed

- Exclude the officially discontinued TED spread from the current Trade Safety
  evidence clock after 31 January 2022. Keep its history, original dates and
  staleness counts, and disclose that it has no direct replacement.
- Require the exact FRED TEDRATE identity and reject observations dated after
  its final published observation on 21 January 2022.

### Changed

- Bind this application source to corpus receipt `r25`; corpus data, independent
  versions, rights and original evidence clocks remain unchanged.

## [0.13.14] - 2026-10-01

### Fixed

- Exclude the officially retired IOER series from current evidence-age clocks
  after validating its identity and historical dates; retain its provenance.
- Label the daily Treasury General Account series as the opening balance
  across the money-market API, website, replay and social cards. Reject closing
  rows from the opening series even when they appear in a cached response.

### Added

- Cited agent and quant integration guides with native framework examples and
  explicit source, freshness and research limits.

### Changed

- Bind this application source to corpus receipt `r24`; the corpus version,
  data, source clocks and rights remain unchanged.

## [0.13.13] - 2026-09-30

### Fixed

- Bound private backup-inspection cache use while preserving complete archive,
  database, receipt, and availability validation.
- Require patched PyJWT in optional OpenBB provider 0.1.1.

### Changed

- Bind the new application source to corpus receipt `r23`; the independently
  versioned corpus and its original evidence clocks are unchanged.

## [0.13.12] - 2026-09-30

### Fixed

- Deliver current public institutional-flow readings across the Hetzner/Railway
  boundary without exposing the private Wake engine pack.
- Include per-section source dates and freshness; omit stale or unavailable
  readings and reject malformed, future-dated or oversized projections.
- Publish the allowlisted projection atomically after successful Wake runs.

## [0.13.11] - 2026-09-29

### Fixed

- Refresh legacy FRED SOFR, EFFR, IORB and overnight reverse-repo caches hourly
  so newly published observations reach the daily funding review promptly.
- Refresh the New York Fed secured-rate and standing-repo caches hourly,
  retaining daily observation cadence, original capture clocks and vintage
  history. Failed refreshes keep stale inputs visibly stale.

### Changed

- Bind the application version with corpus receipt `r21`; existing corpus data,
  source clocks and rights retain their original evidence boundaries.

## [0.13.10] - 2026-09-29

### Fixed

- Poll US funding sources hourly while retaining their native daily observation
  cadence, and bound inherited daily deadlines without bypassing circuit holds.
- Preserve canonical business-date labels when evaluating publication clocks;
  US daily observations with missed releases can no longer report fresh.
- Evaluate TGA against Treasury's next-business-day 16:00 New York deadline,
  including federal holidays and daylight saving time, at build and read time.
  Scheduled deadlines remain separate from unknown actual publication times.
- Refresh the legacy TGA cache hourly so the desk can collect newly released
  statements without waiting for the auctions cache's six-hour interval.
- Align backend bot regressions with the merged research menu and community
  links while retaining the single daily channel-post constraint.
- Close failed MCP meter connections and return explicit unavailable errors
  during SQLite contention without running unmetered tools or repeating the
  storage timeout for every message in a batch.
- Exclude live SQLite databases and sidecars from the recovery tree copy;
  create database snapshots only through the online backup API, close all
  handles explicitly, and give waiting writers a longer yield between steps.
- Poll the sparse daily OFR GCF rate and volume feeds hourly so a new actual
  print can replace older context promptly; no-print dates remain missing.
- Require signed installation and attestation-policy data for the actual native
  recovery deployment before it can lock, sign or export; missing admission
  waits within a fixed bound and invalid admission fails closed.

### Changed

- Bind the application version with corpus receipt `r20`; existing corpus data,
  source clocks, and rights retain their original evidence boundaries.

## [0.13.9] - 2026-09-27

### Added

- Ten homepage funding analytics views built from 18 eligible source histories,
  including overnight rates, exact-date spreads, balance-sheet series, commercial
  paper premiums, a common-date Treasury curve, SOFR changes and repo allocation.
- Keyboard chart inspection, expandable views, source tables and CSV/SVG export.
  Missing observations remain unavailable and source dates and units stay visible.

### Fixed

- Include the reviewed Caddy route-order correction that preserves Undertow
  service routes when Seiche's proxy configuration is sorted.

### Changed

- Bind the new application version with corpus receipt `r19`. Existing corpus
  data, rights and source clocks are unchanged; prior signed tags remain immutable.
  Runtime, package, registry and homepage publication require fresh release proof.

## [0.13.8] - 2026-09-25

### Fixed

- Accept a signed stopped-source proof for Railway instances marked `REMOVED`,
  allowing a new application transition after a workspace spending-limit shutdown.
  Scope, signature, unique instance identities and current-data checks still apply.

## [0.13.7] - 2026-09-25

### Added

- A shared dark research workspace with connected product navigation,
  source-dated economic charts, keyboard inspection and pausable motion.
- Typed, bundled chart and flow components, with validation of source dates,
  units, numeric observations and redistribution eligibility.

### Fixed

- Retain the last verified funding board during temporary API failures and
  recover through bounded requests and visibility-aware refreshes.
- Check publication credentials before expensive collection and build work.

### Changed

- Bind this application source with corpus receipt `r18`, preserving existing
  corpus data, source clocks, rights and immutable older release artifacts.
- Support separately built, signed frontend publication while preserving the
  original engine's generated evidence and validating the complete source history.

## [0.13.6] - 2026-09-24

### Fixed

- Align publication tests with the current product navigation and fallback shell.
  Require generated board and dispatch evidence inside `noscript`, preserve the
  existing shell bytes and keep source-owned social metadata unchanged.
- Scope simulated storage and heartbeat errors to their probe calls so memory
  profiler cleanup can perform its own filesystem operations normally. Production
  no-fallback, error-reporting and write/fsync/close assertions remain enforced.

### Changed

- Prepare a new signed software baseline for the corrected publication suite.
  Corpus receipt `r17` binds this application source without changing the corpus
  version, data, rights or source clocks. Existing 0.13.5 artifacts remain intact;
  activation and publication require fresh recovery and exact-source proof.

## [0.13.5] - 2026-09-24

### Fixed

- Bound the HiGHS solver to one native thread per persistent API worker. Repeated
  quantile fits retain their constraints, results, warnings and failure behavior
  without accumulating a separate default solver pool for every worker thread.

### Changed

- Prepare a new immutable software release for the solver correction while
  retaining the published 0.13.4 artifacts. Corpus receipt `r16` binds the new
  application source without changing the independently versioned dataset or
  its source clocks. Deployment requires fresh recovery and exact-source proof.

## [0.13.4] - 2026-09-15

### Fixed

- Run blocking source-cache operations in a bounded worker pool so they cannot
  stall the API request loop or occupy its health-check workers.
- Keep the last complete snapshot available while replacement evidence is
  saved, then publish the snapshot and readiness evidence together.
- Verify refreshed BIS data against its current materialization proof while
  retaining the immutable signed release baseline and rejecting count regressions.

## [0.13.3] - 2026-09-14

### Fixed

- Batch the coverage endpoint's sealed snapshots, collector states and historical
  record counts. PostgreSQL uses four read connections instead of one set per
  market; source clocks, private snapshot suppression, per-market faults and
  records outside the current market registry retain their existing meanings.
- Evaluate the explicitly identified FRED H.10 daily FX and dollar-index series
  against their weekly publication schedule, including Monday federal holidays.
  Preserve observation/fetch dates and daily frequency; expose scheduled release
  metadata separately from actual publication evidence. Cached trade-safety
  context reevaluates this policy without granting execution authority.

### Changed

- Prepare software, package and discovery metadata for `0.13.3`. The independent
  corpus receipt and direct-OFR observations, source identities and clocks stay
  unchanged. Dataset documentation follows the software tag. Activation and
  publication require fresh exact-source recovery and distribution proof.

## [0.13.2] - 2026-09-13

### Fixed

- Avoid repeated source-rights classification during a single snapshot scan.
  The bounded cache retains full traversal and checks current export permission
  for every request.
- Describe a level anomaly as a level when its observed change rounds to zero.
  Preserve Sonar's observed `last` values in analytical stories while retaining
  explicit zero values and each observation's date.
- Preserve the recovery probe's total deadline while allowing healthy responses
  that take more than five seconds after a bounded connection attempt.

### Changed

- Align software, package and discovery metadata with `0.13.2`. Corpus receipt
  `r13` records the same evidence generation; dataset values, hashes and original
  source clocks remain unchanged. Activation and publication require fresh
  exact-source recovery and distribution proof.

## [0.13.1] - 2026-09-09

### Added

- Add a bounded NY Fed historical importer that verifies the retained archive,
  reparses native annual responses, appends missing observations and resumes
  without replacing existing live vintages or duplicating completed imports.

### Fixed

- Preserve unknown source publication clocks in canonical observations and
  migrate existing SQLite and PostgreSQL stores without changing existing rows,
  evidence hashes or indexes. Use consistent ordering for nullable clocks.
- Keep actual ingestion times on archived data, native missing percentiles and
  the modern EFFR methodology cutoff. History does not become fresh or available
  in past knowledge cutoffs; forecast packs still require known publication times.

### Changed

- Align distribution metadata with `0.13.1` and corpus publication receipt `r11`.
  Existing corpus and direct-OFR dataset identities and content hashes are unchanged.

## [0.13.0] - 2026-09-09

### Added

- Add the WORKBENCH workspace and bounded REST/MCP contract for funding,
  forex comparisons, source-dated history, structured exports and accepted
  Palimpsest China economic context.
- Retain 23 additional NY Fed distribution and volume series, for 34 canonical
  instruments, and collect official ECB FX history with 29 current currencies.
- Preserve China annual-data clocks, rights, selected histories and explicit
  coverage gaps. Numerical production activation remains independently gated.

### Fixed

- Commit ECB currencies and capture evidence atomically. Reject older upstream
  dates and conflicting refreshes; read values and manifests as one generation.
- Keep exact-date currency crosses, missing legs, stale observations and revoked
  export rights explicit. Expose document-specific capture hashes and coverage.
- Advance official collection windows on each scheduled fetch, including NY Fed
  end dates. Keep each paginated request on one interval and historical backfill
  cutoffs fixed when a worker crosses a day, month or year boundary.
- Initialize missing added NY Fed fields within the supervised worker's first
  collection pass. Preserve normal due times and circuit protections; persisted
  observation coverage prevents completed groups from refreshing on every restart.

### Changed

- Align software discovery and metadata with version `0.13.0` and new corpus
  publication receipt `r10`. Existing corpus and direct-OFR dataset identities,
  values and content hashes remain unchanged.

## [0.12.6] - 2026-09-09

### Fixed

- Export successful NY Fed funding observations when collection completes and
  recover completed but unexported work after a collector restart. Preserve the
  original source, observation and collection clocks.
- Restore the authenticated private world-model delivery route after Railway
  cutover with an isolated read-only relay.

## [0.12.5] - 2026-09-08

### Added

- Add the connected research API, `research_network` MCP tool, `seiche research`
  CLI and RESEARCH workspace. All Palimpsest catalog entries retain their source
  rights, access states and clocks, alongside separately dated Seiche funding
  context and explicit routes into LiquiLens, Undertow and NarcoScope.
- Keep the connection contextual: no common score, inferred causation, training
  permission or execution authority is created.

### Fixed

- Run synchronous health validation outside the shared API request loop and
  bound PostgreSQL heartbeat connection and query waits. Preserve the existing
  readiness checks, publication-rights audit and sanitized unknown worker state.
- Use Palimpsest's published JSON registry for catalog handoffs while its native
  catalog MCP addition awaits production approval.

## [0.12.4] - 2026-09-05

### Fixed

- Batch the global money-market atlas's selected PostgreSQL histories into one
  query instead of opening a connection for each market. Keep the same knowledge
  and event cutoffs, latest-vintage selection, native histories and rights gates.
- Fall back to individual market reads when a batch fails, preserving healthy
  markets and the existing sanitized per-market fault contract. No source
  collection, response schema change or cache-policy change is introduced.
- Synchronize the Railway test gate's PostgreSQL dependency contract across its
  workflow, gate image and remote verifier.

### Changed

- Align software discovery and scientific metadata with version `0.12.4`, and
  use corpus publication receipt `r7`. Corpus version `1.0.0` and the audited
  direct-OFR dataset `0.1.0-draft` retain their existing identities and contents.

## [0.12.3] - 2026-09-05

### Added

- Deepened the existing USD money-market desk with observed-print funding
  persistence, SOFR/IORB/EFFR transmission spreads, and qualified month/quarter-end
  comparisons. Independent source clocks, missing dates and minimum sample sizes
  remain explicit in REST and the existing `money_market_context` MCP tool.
- Added signed application updates on Railway that preserve current data, bind
  the replacement deployment, and retain immutable migration and recovery history.

### Fixed

- Aligned portable recovery tools with PostgreSQL 18 and added Linux signature
  verification, including rejection of tampered approvals, to the real image test.
- Waited for documented startup readiness and completed recovery inspection while
  retaining exact identity, freshness and receipt checks.
- Copied SQLite backups in bounded batches so API metering can write between
  batches; archived completed predecessor requests before accepting new authority.

## [0.12.2] - 2026-09-05

### Fixed

- Give the Railway control API read access to its root-owned proposal and journal
  directories so metadata validation, durable submission, and exact replay work
  under the actual runtime UID. Root remains the only journal writer; signed
  operation, release, deployment, and freshness checks are unchanged.
- Exercise submission, fsync, root promotion, replay, and attempted journal
  mutation across real Linux UID boundaries in CI. This covers the permission
  failure that same-user tests could not detect.

## [0.12.1] - 2026-09-05

### Added

- Added an exact-SHA, project-scoped Railway stateful control transport for
  domainless shadows, read-only cutover candidates, writer activation, native
  backup proof, locked off-site recovery export, and post-cutover monitoring.
- Added a public agent-evidence route visualization that distinguishes evidence
  discovery, policy evaluation, receipts, and broker-owned execution controls.

### Fixed

- Accepted the narrowly bounded FRED IORB scheduled-effective-date convention
  without permitting arbitrary future evidence clocks; the prior collection
  timestamp remains the evidence clock and all other future values fail closed.
- Bound archive runtime identity to verified release receipts, aligned the
  PostgreSQL client ABI, corrected shadow entrypoint dispatch, normalized empty
  Railway domain wrappers, retried transient read-only status polls, and
  required populated databases before shadow or candidate acceptance.

### Security

- Kept Hetzner as sole writer through three independently receipted shadows.
  Railway activation requires an exact candidate receipt, a short-lived signed
  grant, authenticated edge proof, recovery evidence, and an explicit host
  authority acknowledgement; no payment or agent route gains order authority.

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

[Unreleased]: https://github.com/beepboop2025/seiche/compare/v0.13.13...HEAD
[0.13.13]: https://github.com/beepboop2025/seiche/compare/v0.13.12...v0.13.13
[0.13.12]: https://github.com/beepboop2025/seiche/compare/v0.13.11...v0.13.12
[0.13.11]: https://github.com/beepboop2025/seiche/compare/v0.13.10...v0.13.11
[0.13.10]: https://github.com/beepboop2025/seiche/compare/v0.13.9...v0.13.10
[0.13.9]: https://github.com/beepboop2025/seiche/compare/v0.13.8...v0.13.9
[0.13.8]: https://github.com/beepboop2025/seiche/compare/v0.13.7...v0.13.8
[0.13.7]: https://github.com/beepboop2025/seiche/compare/v0.13.6...v0.13.7
[0.13.6]: https://github.com/beepboop2025/seiche/compare/v0.13.5...v0.13.6
[0.13.5]: https://github.com/beepboop2025/seiche/compare/v0.13.4...v0.13.5
[0.12.6]: https://github.com/beepboop2025/seiche/compare/v0.12.5...v0.12.6
[0.12.5]: https://github.com/beepboop2025/seiche/compare/v0.12.4...v0.12.5
[0.12.4]: https://github.com/beepboop2025/seiche/compare/v0.12.3...v0.12.4
[0.12.3]: https://github.com/beepboop2025/seiche/compare/v0.12.2...v0.12.3
[0.12.2]: https://github.com/beepboop2025/seiche/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/beepboop2025/seiche/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/beepboop2025/seiche/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/beepboop2025/seiche/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/beepboop2025/seiche/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/beepboop2025/seiche/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/beepboop2025/seiche/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/beepboop2025/seiche/releases/tag/v0.9.1
