# Seiche Governance

## Purpose

Seiche is an open-source public-evidence terminal for dollar funding, money,
foreign-exchange, capital-market, and cross-market transmission research. This
document explains how repository decisions are made and how release authority
is separated from ordinary contribution.

Seiche is currently maintainer-led. It is not represented as a foundation,
standards body, cooperative, or multi-member steering committee.

## Roles

### Users

Users run the software, read the public research surfaces, or call the API and
MCP interfaces. They can report defects, question evidence, and propose changes.

### Contributors

Contributors submit issues, documentation, code, tests, research, or source
reviews. A contribution does not by itself confer merge, publication, or
production access.

### Maintainers

Maintainers triage issues, review changes, enforce repository policy, and merge
accepted work. The current repository owner and default CODEOWNER is
[`@beepboop2025`](https://github.com/beepboop2025). Additional maintainers may be
named in `CODEOWNERS` through a reviewed governance change.

### Release owner

The release owner controls versioning, signed release identity, package and MCP
Registry publication, production deployment, rollback, and release receipts.
Those privileges are deliberately narrower and more sensitive than ordinary
merge access. Automation identities receive only the permissions required for
their bounded workflow.

## Decision process

Routine fixes are decided through pull-request review. Consensus is preferred,
but the maintainer has final merge responsibility and may decline a change that
cannot be operated safely or maintained sustainably.

Open an issue before implementing a material change to any of the following:

- public API, MCP, schema, catalog, or evidence contracts;
- methodology, model eligibility, or claims about historical performance;
- data-source rights, licensed inputs, or public redistribution;
- authentication, billing, privacy, or telemetry;
- release, deployment, backup, or rollback controls; or
- product ownership boundaries with LiquiLens or another sibling repository.

Decisions should record the problem, alternatives, evidence, operational cost,
failure mode, and rollback path. A source being convenient is not sufficient:
authority, cadence, revision behavior, and rights must be explicit.

Security vulnerabilities are handled privately under [SECURITY.md](SECURITY.md),
not debated in a public design issue before a fix is available.

## Review and merge

The `CODEOWNERS` file identifies sensitive and general ownership boundaries.
Repository rules should require CODEOWNER review and successful checks before
changes reach the default branch. A pull request should remain narrow, tested,
and traceable to an issue or clearly stated problem.

No reviewer should approve their own high-risk release-control change without a
second qualified review when another maintainer is available. When only one
maintainer is available, the change must be especially explicit about tests,
immutable inputs, rollback, and post-deployment verification.

## Release and deployment policy

The authoritative host-controller details live in
[`ops/deploy/RELEASE-POLLER.md`](ops/deploy/RELEASE-POLLER.md). Governance-level
release invariants are:

1. The candidate is an exact immutable Git commit, not a moving branch name.
2. Version sources, public catalogs, package metadata, and registry metadata
   agree before publication.
3. The release identity satisfies the configured author and signature policy.
4. Tests run against an isolated candidate before production credentials or
   mutable deployment actions are available.
5. Published artifacts carry checksums or attestations bound to the candidate.
6. Deployment activates the reviewed SHA, proves public health, records an
   immutable receipt, and retains a rollback path.
7. Generated mirrors and cross-repository catalog copies are verified against
   their canonical source bytes.
8. Superseded candidates stop rather than racing a newer default branch.

Only one production controller may own a deployment surface at a time. Hosted
workflow deployment and the host release poller must not both be enabled as
independent authorities.

Tags and published package or MCP Registry versions are immutable. Corrections
ship as a new version; existing release identities are not moved or overwritten.

## Evidence and product boundaries

Seiche's code is AGPL-3.0-or-later, but upstream data retains its own terms. A
governance decision cannot reclassify restricted data as public merely because
the code can fetch it. Public outputs must retain source clocks, evidence
statuses, rights boundaries, and methodological limitations.

Seiche owns its dollar-funding collection, research, history, and publication
artifacts. Cross-product integrations should use documented HTTP, MCP, or
artifact contracts instead of copying private implementation or silently
creating a new source of truth.

## Conflicts of interest

Contributors and reviewers should disclose financial, employment, vendor, data
licensing, or personal interests that could reasonably affect a decision. A
maintainer may ask an interested reviewer to abstain or require an additional
review. Disclosure does not automatically disqualify a contribution; hidden
influence is the larger risk.

## Maintainer changes

New maintainers are selected by the current maintainer based on sustained,
constructive contributions, sound judgment around evidence and operations, and
willingness to protect users and source rights. Access should be granted at the
least privilege needed and removed promptly when responsibilities end.

If the current maintainer becomes unavailable, a successor should be chosen
from established contributors through a public issue and a reviewed update to
this document and `CODEOWNERS`. Signing keys, deployment credentials, and
service ownership must be transferred out of band and never committed.

## Changing this document

Governance changes use the normal pull-request process, must state their effect
on authority and release safety, and require maintainer approval. Material
changes should remain open long enough for affected contributors to comment,
except for urgent security repairs that are disclosed after risk is contained.
