# Contributing to Seiche

Seiche welcomes focused fixes, new public-data adapters, research improvements,
documentation, and integrations. The most useful contributions preserve the
project's central contract: every conclusion carries its evidence clock,
provenance, status, and limitations.

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- Use a public issue for bugs, data-quality reports, and feature proposals.
- Use a [private security advisory](https://github.com/beepboop2025/seiche/security/advisories/new)
  for vulnerabilities; see [SECURITY.md](SECURITY.md).
- For a substantial new engine, data source, public contract, or operational
  dependency, open an issue first so its evidence and maintenance boundaries can
  be agreed before implementation.

## Repository map

- `backend/seiche/` contains the Python engines, API, MCP server, and source
  adapters.
- `backend/tests/` contains the behavioral and release-contract tests.
- `frontend/` contains the TypeScript/Vite interface and public static assets.
- `bot/` contains Telegram delivery code.
- `integrations/` contains bounded third-party integration kits.
- `ops/` contains production, release, backup, and edge operations.
- `server.json` is the official MCP Registry manifest.

Read [README.md](README.md) for the product surface and [DESIGN.md](DESIGN.md)
for historical design context. Operational publishing is documented in
[`docs/PUBLISHING.md`](docs/PUBLISHING.md).

## Development setup

Seiche requires Python 3.12 or newer. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e './backend[dev,collectors]'
.venv/bin/python -m pytest backend/tests -q
```

For the frontend:

```bash
cd frontend
npm ci
npm run build
```

Use `python3` or the virtual-environment interpreter explicitly. Do not assume a
`python` executable exists.

## Making a change

1. Branch from the current default branch.
2. Keep the change narrow and explain the user-visible outcome.
3. Add or update tests for behavioral changes.
4. Update contracts and documentation in the same pull request when an API,
   MCP tool, schema, source policy, or operational assumption changes.
5. Run the smallest relevant tests while iterating, then the full applicable
   checks before requesting review.

Good commits are reviewable and describe why the change is needed. Do not mix
format-only changes, generated market snapshots, or unrelated refactors into a
functional patch.

## Evidence and data-source rules

New or changed data sources must document:

- the official or canonical source URL;
- publication cadence and the meaning of the source timestamp;
- access and redistribution rights;
- the fail-closed behavior when the source is unavailable or stale;
- units, transformations, and revision behavior; and
- whether the evidence is point-in-time, current-vintage, derived-only,
  metadata-only, licensed, or prohibited from public output.

Never commit credentials, account data, restricted raw observations, production
databases, or caches. The AGPL license for Seiche code does not override an
upstream provider's terms. Missing evidence must remain visible as missing; it
must not be silently converted into a neutral reading.

## Testing expectations

At minimum, run the checks that cover the changed surface:

```bash
# Targeted backend test
.venv/bin/python -m pytest backend/tests/test_relevant_module.py -q

# Full backend suite
.venv/bin/python -m pytest backend/tests -q

# Frontend typecheck and production build
npm --prefix frontend ci
npm --prefix frontend run build
```

Changes to release, deployment, MCP, API, catalog, or source-policy behavior need
their dedicated contract tests as well. Network-dependent tests should be
bounded, polite, and explicit about whether they are optional live probes or
deterministic CI gates.

## Pull requests

Complete the pull-request template. Reviewers should be able to identify:

- the problem and intended behavior;
- the exact validation performed;
- changes to public schemas, claims, data rights, or evidence clocks;
- deployment, migration, rollback, or catalog-sync implications; and
- any owner-controlled action required after merge.

Maintainers may request a smaller patch, additional provenance evidence, or a
reproducible fixture before merging. Version bumps, tags, package publication,
registry publication, and production deployment are release-owner actions; do
not combine them with an ordinary contribution unless the change is explicitly
being prepared as a release.

By contributing, you agree that your contribution is licensed under the
repository's [AGPL-3.0-or-later license](LICENSE). No contributor license
agreement is currently required.

All participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
