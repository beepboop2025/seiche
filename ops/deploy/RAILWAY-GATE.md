# Attested Railway release gate (phase 1)

This phase moves only the memory-instrumented backend admission suite off the
shared Hetzner host. It does not move Seiche's API, PostgreSQL, SQLite state,
NBS evidence store, workers, Caddy routes, backups, snapshot activation, or
rollback authority.

```text
exact main SHA + tree
        |
        v
GitHub creates canonical `git archive` bytes
        |
        v
Railway runs the exact memray pytest command
        |
        v
GitHub validates the exact Railway deployment result
        |
        v
one-file OCI artifact + GitHub OIDC provenance
        |
        v
Hetzner independently verifies repo/workflow/ref/SHA/tree/archive/digests
        |
        v
root-owned gate receipt v2 -> existing deploy/health/rollback transaction
```

## Tracked contract

- `.github/workflows/railway-release-gate.yml` serializes a dedicated Railway
  service, uploads an exact `git archive`, waits for the exact deployment to
  reach `SUCCESS`, extracts one canonical result from that deployment's logs,
  publishes it to `ghcr.io/beepboop2025/seiche-release-gates`, OIDC-attests the
  immutable manifest, and proves anonymous retrieval.
- `ops/railway/Dockerfile.gate` pins the Python 3.12 base image, an immutable
  Debian snapshot, and the same checksum-pinned Caddy adapter used in CI. It
  installs `backend[dev,collectors]` non-editably from one extraction, then
  runs the tests against a second clean, root-owned, read-only extraction. The
  image deliberately includes Git, OpenSSH, systemd-analyze, util-linux and
  procps because the full deploy-contract suite invokes those host tools.
- `ops/railway/run-gate.py` re-hashes the source archive, runs exactly
  `python -m pytest backend/tests -q --memray -o faulthandler_timeout=300`,
  records the Railway deployment/project/environment/service IDs, test counts,
  Python version, and dependency snapshot digest, then exposes `/healthz` only
  after the gate is complete. Railway therefore cannot mark the deployment
  successful before the suite is green.
- `ops/deploy/seiche-remote-gate-verify.py` resolves the SHA tag to an immutable
  OCI digest, validates the one-layer artifact, runs `gh attestation verify`
  anonymously with exact workflow/source constraints, recreates the local Git
  archive digest, and renders `seiche.release-receipt.v2`.
- `ops/deploy/seiche-release-poll.sh` defaults to that remote verifier. The
  release receipt continues to hash the root-owned gate receipt, preserving the
  gate-to-release evidence chain.

Railway never receives a production database URL, API credential, deploy key,
Telegram token, NBS signing key, GitHub package token, or Hetzner credential.
The only Railway secret involved is Railway's own project-scoped control token,
and it stays in the protected GitHub `railway-gate` environment.

## One-time Railway bootstrap

1. Create a dedicated project (or isolated service) named `seiche-release-gate`
   in the paid workspace. Do not attach a volume, database, public domain, or
   GitHub autodeploy source. The workflow uploads each exact source bundle with
   `railway up`; a connected source would create an untracked competing deploy.
2. Give the service one replica in the desired region and enough CPU/RAM for the
   memray suite. Start with 8 vCPU and 16 GiB RAM, then use the first three gate
   receipts and Railway metrics to right-size it. Pro billing removes account
   limits; resource sizing still must be set on the service. Do not define
   `RAILWAY_RUN_UID` or any pytest/Python override variables. The runner requires
   effective identity `65532:65532` and supplies pytest a reviewed minimal
   environment, so a dashboard variable cannot select or inject tests.
3. Create a project-scoped Railway token for this dedicated project/environment.
   In the GitHub `railway-gate` environment, add these secrets:

   - `RAILWAY_TOKEN`
   - `RAILWAY_PROJECT_ID`
   - `RAILWAY_ENVIRONMENT_ID`
   - `RAILWAY_SERVICE_ID`

   The IDs are treated as secrets so the workflow never relies on mutable names.
   Do not add them to repository files or Railway service variables.
4. Ensure the existing `ghcr-release` GitHub environment permits this workflow's
   source-free publication job. Make the
   `ghcr.io/beepboop2025/seiche-release-gates` package public after its first
   successful publish; the production host deliberately verifies anonymously.
5. Manually dispatch `railway-release-gate` on `main`. Bootstrap is complete only
   after all three jobs are green and anonymous OCI plus attestation verification
   passes. A queued Railway build or a green test log alone is not proof.

The tracked Railway config uses a one-hour health-check window and
`restartPolicyType=NEVER`. A red test never emits a receipt, never serves
`/healthz`, and must leave the exact Railway deployment `FAILED` rather than
restarting into ambiguous logs.

The host timer normally reaches a new SHA before its Railway artifact exists.
An exact GHCR 404 is therefore a non-mutating deferral, not an incident: the
controller records a root-owned first-seen marker and retries without running
the local suite. If the artifact is still absent after 3,600 seconds, the
controller fails and alerts. Unauthorized, malformed, unreachable, mismatched,
or unverifiable evidence fails immediately. The pending SLO can be reviewed via
`SEICHE_CONTROL_REMOTE_GATE_PENDING_MAX_SECONDS` (300--86,400 seconds); changing
it never enables fallback.

## One-time Hetzner verifier bootstrap

The signed controller installer installs the verifier but intentionally does not
download trust tools. Provision the reviewed binaries independently, verify their
published checksums, and keep them root-owned and non-writable:

```bash
set -euo pipefail
TOOL_STAGE=$(mktemp -d /root/seiche-gate-tools.XXXXXX)
curl --fail --location --proto '=https' --tlsv1.2 \
  https://github.com/regclient/regclient/releases/download/v0.11.5/regctl-linux-amd64 \
  --output "$TOOL_STAGE/regctl"
echo "c93aa7638749f5aaac1a8e01787321889c78f0101809bb2880343478d0ba0467  $TOOL_STAGE/regctl" |
  sha256sum --check --strict
curl --fail --location --proto '=https' --tlsv1.2 \
  https://github.com/cli/cli/releases/download/v2.98.0/gh_2.98.0_linux_amd64.tar.gz \
  --output "$TOOL_STAGE/gh.tar.gz"
echo "3b8ac6b30336802fc1a858d7c084e11cdf24ac1a761ca90b68022d7d729208de  $TOOL_STAGE/gh.tar.gz" |
  sha256sum --check --strict
tar --extract --gzip --file "$TOOL_STAGE/gh.tar.gz" \
  --strip-components=2 --directory "$TOOL_STAGE" gh_2.98.0_linux_amd64/bin/gh
install -o root -g root -m 0755 "$TOOL_STAGE/regctl" /usr/local/bin/regctl
install -o root -g root -m 0755 "$TOOL_STAGE/gh" /usr/local/bin/gh
/usr/local/bin/regctl version
/usr/local/bin/gh version
```

Retain the staging directory until the first verifier smoke is green, then
remove it through the host's reviewed cleanup process. Do not authenticate either
tool: the gate artifact and its Sigstore bundle must be publicly verifiable.

Install the controller from the exact signed asset root as described in
`RELEASE-POLLER.md`, leave the timer disabled, and run:

```bash
/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  SEICHE_CONTROL_GATE_ONLY=1 \
  /usr/bin/bash -p /usr/local/sbin/seiche-release-poll
```

That smoke must create a mode-0400, root-owned v2 gate receipt whose
`gate_provider` is `railway`, while the deployed marker and live checkout remain
unchanged.

## Fail-closed and break-glass behavior

The default controller never runs the local full suite. Missing CLI tools,
private OCI content, a tag/digest mismatch, malformed result, wrong repository/
workflow/ref/SHA/tree/archive, red conclusion, or failed OIDC verification
stops the release before checkout mutation. The one narrow exception is a 404
for the not-yet-published exact-SHA tag during the bounded pending window above;
it only defers. There is no automatic fallback.

An operator may deliberately select the old local gate during a Railway/GitHub
incident. Disable the timer first, record the incident and reviewed SHA, confirm
the host is quiet, then invoke the exact controller with:

```bash
/usr/bin/env -i \
  HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  SEICHE_CONTROL_LOCAL_GATE_BREAK_GLASS=1 \
  /usr/bin/bash -p /usr/local/sbin/seiche-release-poll
```

That path still performs signature, supersession, full memray suite, admission,
health, receipt, and rollback checks. Its v2 receipt is visibly marked
`local-break-glass`; it cannot be confused with attested Railway evidence.

## Phase-1 limitations

- Railway transports its result through the exact deployment's retained logs.
  GitHub requires exactly one base64 canonical marker and binds it to the
  deployment IDs before attestation. A later phase can replace this transport
  with a short-lived authenticated result channel without changing the receipt.
- Python and the base image are pinned, but backend dependencies remain ranges.
  The receipt records the resolved dependency snapshot digest; adding a reviewed
  lock file is the next reproducibility improvement.
- Phase 1 trusts the SSH-authorized `main` commit to define this workflow,
  Dockerfile, and runner. GitHub OIDC proves exactly which target-controlled
  workflow packaged the result; it does not independently prove Railway CPU
  execution against a workflow maintained outside the candidate. Keep the
  `railway-gate` and `ghcr-release` environments protected, require review for
  changes to these gate files, and move the producer to a separately governed
  reusable workflow before broadening the signing-authority set.
- The runner is intentionally Linux/amd64 because the reviewed Caddy binary is
  amd64. The Docker build fails on another architecture instead of silently
  running an emulated or mismatched adapter.
- The OCI attestation covers the canonical receipt manifest, not a Railway-built
  container image digest. Exact source archive, tree, runner base image, tool
  bootstrap, deployment IDs, and dependency snapshot are bound separately by
  the reviewed contract.
- This phase removes the duplicate on-host gate and its load cooldown. Snapshot
  assembly still runs on Hetzner and remains the largest post-gate release step.
  Remote snapshot construction/import is a separate phase with a separate
  evidence and state-authority review.
- Do not promise a Railway duration before benchmarking the configured service.
  Record queue, image-build, pytest, packaging, host-verification, and deployment
  times for at least three exact SHAs; then set an operational SLO from observed
  p95 rather than plan tier alone.
