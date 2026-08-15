# RBNZ B2 access incident — 2026-08-14

## Decision

Seiche does not work around RBNZ's automated-access controls. RBNZ's current
[terms of use](https://www.rbnz.govt.nz/about-our-site/terms-of-use) allow
automated website access only to public search engines or with RBNZ's prior
written permission. Reuse permission for published statistics is separate from
permission to retrieve the website with a bot.

The NZ adapters therefore fail closed before making a network request unless
production contains an auditable reference to written approval and a bounded
re-review date. After approval, Seiche retains its honest named collector user
agent and expects RBNZ to allow-list the approved production public IP. A
browser-impersonation or Cloudflare-challenge workaround is not an acceptable
fallback.

## Cause and reproduction

From the Hetzner collector egress, both the canonical B2 workbook and canonical
HTML page returned Cloudflare HTTP 403 to Seiche's named research-collector user
agent. The supervisor retry budget was already zero, so each adapter made one
workbook request and one HTML fallback request and then recorded a scoped
failure.

During diagnosis, a coherent Chrome request profile returned the public
workbook with HTTP 200, XLSX media type, 445,574 bytes, ZIP magic `504b0304`,
and SHA-256
`16ae1f86086ba553a23b3be5e7469f50153215278ec5f74df230f92b2aa969a8`.
That experiment established the technical cause, but it is not deployment
authority and the browser profile is deliberately absent from production code.
The public workbook hash is incident evidence, not a permanent content pin;
RBNZ updates the workbook daily.

No separate first-party machine API or official distribution channel for these
three B2 series was identified. RBNZ publishes an
[allow-list request form](https://www.rbnz.govt.nz/about-our-site/terms-of-use/allow-list-request)
for automated agents. Submitting that form is an external organisational action
and requires a real name, organisation, contact email, reason, and production
public IP; those details must not be invented by deployment automation.

## Parser and transport boundary

The current workbook uses a multi-row `Data` sheet. Seiche validates the exact
RBNZ series IDs `INM.DP1.N`, `INM.DD1.N`, and `INM.DD2.N`. A modern workbook
with a missing, renamed, incomplete, or duplicate ID contract cannot downgrade
to fuzzy display-heading parsing. Legacy parsing is limited to an explicit
`Date`-first layout with no non-empty preamble and an immediately following
dated row.

The approved transport is bounded and fail closed:

- only the canonical `rbnz.govt.nz` HTTPS hosts and default port are accepted;
- redirects are not followed;
- transport compression is requested as `identity` and any encoded response is
  rejected before reading; raw response bodies are limited to 8 MiB;
- XLSX member count, expanded size, sheet count, row count, and column count are
  capped before or during parsing;
- exactly one workbook request is allowed, followed by at most one canonical
  HTML request; and
- the HTML summary remains ineligible for historical backfill.

Historical observations and forward ledgers are immutable. Recovery uses the
normal append-only backfill path and never issues corrective SQL or rewrites an
earlier failed run.

## Approval configuration

Do not create this configuration merely to make a test or backfill pass. After
RBNZ supplies written permission covering the production public IP and intended
cadence, retain the approval artifact in root-controlled records, hash it, and
install `/etc/seiche/rbnz-access.env` with exactly:

```text
SEICHE_RBNZ_ACCESS_APPROVAL_SHA256=<64 lowercase hex characters>
SEICHE_RBNZ_ACCESS_APPROVAL_VALID_UNTIL=<YYYY-MM-DD, no more than 366 days ahead>
```

The file must be a non-symlink regular file owned by `root:seiche` with mode
`0640`. The date is an operational re-review deadline; renew it only after
confirming that the approval, public IP, terms, and collection cadence still
match. The installer validates the file's exact shape and permissions. The
worker and backfill units load it optionally; if it is absent, malformed,
expired, or more than 366 days ahead, both RBNZ adapters stop before network
access and append an explicit `UNAVAILABLE` collector outcome. This policy
outcome has zero acquisition attempts, clears legacy failure-circuit state,
and does not count as source instability. It still appears in market fault
payloads, keeps affected inputs stale or unavailable, and never creates a
backfill completion marker.

If a backfill invocation has no remaining adapter except policy-unavailable
RBNZ sources, the oneshot exits successfully because it enforced the legal
boundary correctly. That green unit state is not evidence of NZ data coverage.
RBNZ HTTP, parser, persistence, and unexpected preflight faults remain
`FAILED`; repeated real source failures can still become `CIRCUIT_OPEN` and
fail the oneshot.

## Regression and recovery checks

```bash
cd backend
.venv/bin/pytest -q tests/test_official_adapters.py \
  tests/test_collector_reliability.py tests/test_market_materialize.py
ruff check seiche/sources/base.py seiche/sources/canonical.py \
  seiche/sources/official.py seiche/collectors.py seiche/cli.py \
  tests/test_official_adapters.py tests/test_collector_reliability.py \
  tests/test_market_materialize.py
ruff format --check seiche/sources/base.py seiche/sources/canonical.py \
  seiche/sources/official.py seiche/collectors.py seiche/cli.py \
  tests/test_official_adapters.py tests/test_collector_reliability.py \
  tests/test_market_materialize.py
```

Deploy only through the reviewed Seiche release workflow. The deploy wrapper
owns writer quiescence, tests, health checks, snapshot handoff, service restart,
and rollback. Do not copy a patched module into production or run a concurrent
manual collector.

Only after written approval and configuration, create a normal market backup,
complete the scratch restore check, and run one controlled NZ backfill. Verify
new append-only run records for both `rbnz_policy` and `rbnz_wholesale`, their
canonical workbook source URI, attempt count of one, positive observation
counts, and raw-capture hashes. Then create and scratch-restore a post-backfill
backup. Successful source collection does not promote the NZ pack; promotion
remains a separate research gate.
