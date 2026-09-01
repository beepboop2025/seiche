# Attested Railway gate and stateful migration (phases 1-7)

Phase 1 moves the memory-instrumented backend admission suite off the shared
Hetzner host. Phase 2 also moves the pure snapshot computation, while retaining
all durable state, evidence sealing, activation, health checks, Caddy routing,
backups, and rollback authority on Hetzner. Phase 3 ends the synchronous
cutover after exact-SHA snapshot, API, and edge health, then seals backup,
isolated restore, worker startup, and recurring-readiness evidence through a
retrying host-local service. None of these phases gives Railway a production
credential or a stateful production role.

Phase 4 adds an isolated stateful shadow on one Railway volume plus Railway
PostgreSQL. It restores exact backup-v4 bytes and serves private compatibility
health only. Hetzner remains the sole writer, public origin, and rollback
authority; Phase 4 contains no cutover path. See
[RAILWAY-STATEFUL-MIGRATION.md](RAILWAY-STATEFUL-MIGRATION.md).

Phase 5 supplies the separately protected writer and edge cutover. Phase 6
adds native backups, PostgreSQL PITR, recurring monitoring, and portable
off-site recovery. Phase 7 supplies an independent authority transfer for the
Seiche Telegram bot's state, long poll, and delivery schedules. These phases
remain inert until their exact runbook gates are completed; merging them is not
an activation. See
[RAILWAY-STATEFUL-CUTOVER.md](RAILWAY-STATEFUL-CUTOVER.md),
[RAILWAY-STATEFUL-RECOVERY.md](RAILWAY-STATEFUL-RECOVERY.md), and
[RAILWAY-TELEGRAM.md](RAILWAY-TELEGRAM.md).

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

exact main SHA + tree
        |
        v
separate Railway service computes a source-only snapshot
        |
        v
GitHub independently validates, packages, and OIDC-attests canonical bytes
        |
        v
Hetzner verifies provenance, rechecks rights, and reseals local evidence
        |
        v
root-selected handoff token -> candidate hydration -> existing promotion

strict exact-SHA health + edge convergence
        |
        v
immutable release receipt -> live cutover returns
        |
        v
host-local recovery service waits for durable workers
        |
        v
release-bound backup + isolated restore + readiness
        |
        v
immutable recovery receipt -> fully recovery sealed

committed exact-SHA backup-v4 + live/recovery receipt digests
        |
        v
operator stages a closed nine-file snapshot on an isolated Railway volume
        |
        v
Railway restores filesystem generations + generation-specific PostgreSQL
        |
        v
strict SQLite/NBS/count proof -> immutable shadow receipt + private health
        |
        v
GitHub independently verifies and OIDC-attests the exact private receipt
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
- `ops/railway/run-gate.py` re-hashes the source archive, proves `seiche`
  imports from the verified `/workspace/backend` tree, binds the read-only Git
  bundle's exact commit/tree to that archive, and runs exactly
  `HOME=/var/lib/seiche-railway-gate-runtime TMPDIR=/var/lib/seiche-railway-gate-runtime/tmp PYTHONPATH=/workspace/backend SEICHE_RUNTIME_DATA_DIR=/var/lib/seiche-railway-gate-runtime/data SEICHE_VALIDATION_DIR=/var/lib/seiche-railway-gate-runtime/data/market-validation python -P -m pytest backend/tests -q --memray -o faulthandler_timeout=300 -o cache_dir=/var/lib/seiche-railway-gate-runtime/pytest-cache`.
  The private external cache, database root, and validation root preserve the
  read-only source invariant. The runner proves both import and data-root
  identity before it records the Railway deployment/project/environment/service
  IDs, test counts, Python version, and dependency snapshot digest, then exposes
  `/healthz` only after the gate is complete. Railway therefore cannot mark the
  deployment successful before the suite is green.
- `ops/deploy/seiche-remote-gate-verify.py` resolves the SHA tag to an immutable
  OCI digest, validates the one-layer artifact, runs `gh attestation verify`
  with a fixed non-secret CLI-preflight sentinel and an empty Docker credential
  store under exact workflow/source constraints, recreates the local Git
  archive digest, and renders `seiche.release-receipt.v2`.
- `ops/deploy/seiche-release-poll.sh` defaults to that remote verifier. The
  release receipt continues to hash the root-owned gate receipt, preserving the
  gate-to-release evidence chain.

## Phase-2 snapshot contract

- `.github/workflows/railway-snapshot-prebuild.yml` serializes a second,
  dedicated Railway service. It builds an exact source archive and canonical
  request, verifies the service-instance health/restart policy, derives its
  unique Railway-generated port-8080 origin from the project-scoped API, and
  waits for deployment- and request-bound `/healthz` bytes. The request carries
  only the SHA-256 digest and expiry of a one-run 256-bit result token. The
  dedicated stable Railway origin is only a route: without that bearer, the
  result path returns a generic 404. After the snapshot is complete, the
  workflow proves the route is closed, retries transient old-route responses,
  downloads the bounded canonical result over authenticated HTTPS, and
  independently validates the bytes before publishing them to
  `ghcr.io/beepboop2025/seiche-release-snapshots`, OIDC-attests the immutable
  manifest, and proves anonymous retrieval.
- `ops/railway/Dockerfile.snapshot` and `ops/railway/run-snapshot.py` run as
  uid/gid 65532 with a root-owned read-only source tree and an isolated runtime
  data directory. The child receives no Railway token, database URL, production
  secret, deploy credential, or writable source path. It can only emit the
  bounded canonical result into `/result`, expose `/healthz` after success, and
  return those exact bytes on the closed bearer route before its request-bound
  expiry. The raw bearer is never persisted by Railway and exists there only
  transiently in the edge and service request; only its digest is stored in the
  image request. The Actions copy is held in a mode-0600 ephemeral runner file.
- `seiche.remote_snapshot_build` calls the normal assembly pipeline with
  `publish=False`. Collection, engines, deep layers, Navigator, sanitization,
  and rights checks still run, but PIT records, notary evidence, SQLite state,
  in-process cache, and release handoffs are not mutated remotely.
- `ops/deploy/seiche-remote-snapshot-verify.py` resolves the exact SHA tag to an
  immutable OCI digest, validates its single canonical layer and OIDC identity,
  binds every source/Railway/dependency/payload digest, and writes the verified
  artifact mode 0600 beneath `/run/seiche-control`.
- `seiche-snapshot-import.service` is the only bridge into state. The
  unprivileged, sandboxed importer repeats schema, freshness, rights, and
  servability checks, creates a host-local evidence seal, and returns only a
  handoff token plus payload digest. The API starts with both values in its
  root-controlled release environment and refuses a changed or generic handoff.
- The root release receipt is v3 on this path and hashes both the Railway gate
  receipt and the Railway snapshot receipt. A missing exact-SHA artifact only
  defers during the bounded publication window; malformed, stale, private,
  mismatched, or unattested content fails closed. No remote error silently
  selects the on-host rebuild.

## Phase-3 fast-cutover contract

- `ops/deploy/seiche-deploy-wrapper.sh` remains the only authority allowed to
  mutate the checkout, activate the imported snapshot, restart the API, deploy
  Caddy, accept strict exact-SHA health, or roll back. Once snapshot, API, and
  market health pass it queues the market and source workers without waiting
  for their initial sweep; edge convergence must still pass before the wrapper
  returns. Backup and restore no longer extend the live-cutover transaction.
- `ops/deploy/seiche-release-poll.sh` durably writes the v3 release receipt
  before queuing `seiche-release-recovery-seal.service`. A valid release receipt
  plus strict health means the SHA is live. It does not mean recovery is sealed
  until the corresponding `*.recovery.json` exists and validates.
- `ops/deploy/seiche-release-recovery-seal.sh` revalidates the root-selected
  release environment and deployed marker before and after every long-running
  stage, then binds the exact v2/v3 release receipt and digest before sealing.
  It waits for both durable workers, reuses a still-valid release-bound recovery proof or creates a new
  backup and isolated restore, converges data readiness without restarting the
  live API, restores the recurring timers, and writes
  `seiche.release-recovery-receipt.v1` atomically with no replacement path.
- The recovery receipt binds the exact commit, tree, release-receipt digest,
  backup snapshot and inventory digest, isolated-restore receipt digest,
  readiness states, off-site schedule state, and completion time. The poller
  independently revalidates canonical bytes, owner/group/mode, link count,
  digest binding, and timestamp order. Missing evidence is pending and retryable;
  existing invalid evidence fails closed.
- The hardened oneshot retries after ten minutes on failure. A later poll also
  queues it when a release is live but recovery evidence is absent. Neither
  retry path restarts the API or rewrites an accepted receipt, so delayed
  recovery work cannot undo a successful cutover.
- A direct SSH fallback can restore workers, backup/restore proof, readiness,
  and recurring timers while its controller receipt is still unavailable. It
  retries only the immutable seal until that receipt arrives; missing evidence
  never weakens the final receipt contract.

## Phase-4 stateful-shadow contract

- `.github/workflows/railway-stateful-shadow.yml` is manual-only and protected
  by `railway-stateful-migration`. It requires an exact committed snapshot and
  explicit `HETZNER_REMAINS_SOLE_WRITER` confirmation.
- `ops/railway/Dockerfile.stateful` carries both a canonical Git archive and Git
  bundle, proves their exact commit/tree/byte identity, and keeps the source
  worktree read-only. The root supervisor alone can restore the mounted volume;
  it starts only the API child as uid/gid 10001.
- `seiche.stateful_migration` accepts the nine-file backup-v4 contract (and
  legacy v3 only as an empty, inactive Palimpsest China state),
  rejects links, traversal, archive aliases, device members, oversized content,
  and unstable files, then performs SQLite, full NBS, PostgreSQL, and count-floor
  verification before writing a receipt.
- Every accepted filesystem and database has a content-derived generation
  name. Restart reuse re-hashes all four trees, repeats SQLite/NBS/Palimpsest
  activation-state checks, and requires unchanged PostgreSQL counts. A receipt
  cannot authorize drift.
- The service has no public domain, worker, publisher, collector, bot, Hetzner
  credential, or production control plane. Its `/healthz` is deployment
  admission and private compatibility evidence, not a public availability SLO.

Railway never receives a Hetzner production database URL, API credential,
deploy key, Telegram token, NBS signing key, GitHub package token, or Hetzner
credential. Phases 1-3 receive no database URL. Phase 4 receives only its
Railway-private PostgreSQL reference; the protected workflows use Railway's own
project-scoped control token.

## Phase-7 Telegram contract

- `.github/workflows/railway-telegram.yml` prepares a dedicated one-volume
  service, freezes and snapshots Hetzner through a bounded forced command,
  restores a non-authoritative candidate, and seals the cutover snapshot under
  external COMPLIANCE Object Lock.
- A separately protected grant starts one unprivileged sequential worker. A
  successful `getUpdates`, non-decreasing offset, completed schedule pass, and
  current heartbeat must all precede the immutable activation receipt.
- Pre-grant rollback first proves no grant exists. After grant, Hetzner remains
  masked and any repair is a forward Railway recovery incident.
- Six-hour monitoring validates the full authority chain, current worker,
  volume identity/headroom, daily/weekly/monthly native backup schedules, fresh
  backup, locked canary, and frozen source host.

Follow `RAILWAY-TELEGRAM.md`. The workflow being present does not mean the bot
has moved or that `RAILWAY_TELEGRAM_PHASE7_ENABLED` may be set.

## One-time Railway bootstrap

1. Create a dedicated project with isolated services named
   `seiche-release-gate` and `seiche-snapshot-prebuild` in the paid workspace.
   Do not attach a volume, database, or GitHub autodeploy source to either
   service, and keep the gate service domainless. Give only the snapshot service
   one Railway-generated HTTPS domain targeting port 8080; the rotating,
   expiring bearer is its result security boundary. Each workflow uploads its
   exact source bundle with `railway up`; a connected source would create an
   untracked competing deploy.
2. Give each service one replica in the desired region and enough CPU/RAM for its
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
   - `RAILWAY_SNAPSHOT_SERVICE_ID`

   The IDs are treated as secrets so the workflow never relies on mutable names.
   Do not add them to repository files or Railway service variables.
   Each workflow checks its four required names before checkout or any network
   action. Missing configuration fails red immediately and reports names only,
   never values.
   The snapshot workflow derives the generated HTTPS origin from the exact
   service instance and rejects custom, multiple, or non-port-8080 domains; no
   separately maintained origin variable is required.
4. Ensure the existing `ghcr-release` GitHub environment permits both workflows'
   source-free publication jobs. Make both
   `ghcr.io/beepboop2025/seiche-release-gates` and
   `ghcr.io/beepboop2025/seiche-release-snapshots` public after their first
   successful publish; the production host deliberately verifies anonymously.
5. Manually dispatch `railway-release-gate` and then
   `railway-snapshot-prebuild` on `main`. Bootstrap is complete only after both
   workflows are green and anonymous OCI plus attestation verification passes.
   A queued Railway build, green test log, or Railway `SUCCESS` alone is not
   proof.

Bootstrap for the stateful shadow is separate because it creates billable,
durable resources and a different protected authority boundary. Follow
`RAILWAY-STATEFUL-MIGRATION.md`; do not attach a volume or PostgreSQL reference
to either stateless Phase 1/2 service.

The tracked Railway config documents the one-hour health-check window and
`restartPolicyType=NEVER`, but Railway no longer applies legacy config-as-code
to newly created services. Set those three service-instance fields during
bootstrap. Both workflows read them back through the project-scoped API before
uploading source and reject `SUCCESS` unless the exact deployment metadata
contains the same values. A red test never emits a receipt, never serves
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

## Phase-1 through Phase-7 limitations

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
- Phase 2 removes the full on-host snapshot build from the normal path, but the
  production host still performs evidence sealing, database persistence, API
  hydration, strict health, snapshot activation, recovery proof, and edge
  convergence. Phase 3 moves worker startup and recovery sealing after the live
  receipt; it does not move production state or rollback authority to Railway.
- Phase 4 proves restore and private read compatibility only. Railway volumes
  constrain the stateful service to a single replica and do not provide
  overlapping deployment semantics. Phase 4 cannot be promoted merely by
  adding a domain.
- Phase 5 implements the explicit maintenance freeze, final delta-free
  snapshot, closed authority fence, authenticated read-only edge transition,
  protected writer grant, immutable activation receipt, and one-way host
  acknowledgement. Follow `RAILWAY-STATEFUL-CUTOVER.md`; do not
  activate it until three Phase 4 canaries and Phase 6 recovery controls
  are green.
- Phase 6 adds separately protected native-backup administration, six-hour
  production/PITR/volume monitoring, and a daily activation-bound backup-v4
  export. Each export pauses only Railway writers, keeps reads online, restores
  the portable bytes in isolation, and seals them outside Railway under
  COMPLIANCE Object Lock. Native-backup bootstrap and a non-production external
  storage preflight happen before Phase 5 activation; the first real export is
  an immediate post-activation seal because it must bind the activation
  receipt. Follow `RAILWAY-STATEFUL-RECOVERY.md`; merging the workflow does not
  enable schedules, move authority, or prove a canary.
- Phase 7 implements the separate `/var/lib/seiche-bot` snapshot, restore,
  delivery-idempotency, Telegram-offset, authority, native-backup, and monitor
  contracts. It remains non-live until the candidate and activation receipts
  in `RAILWAY-TELEGRAM.md` exist. Its immutable external snapshot is a cutover
  canary, not a recurring portable export of future bot-state changes.
- Phase 7 pins the Telegram service to its activation SHA. It does not yet
  implement an authority-preserving in-place bot-code upgrade.
- Rissaga/Hermes and `/var/lib/rissaga` remain an adjacent state and publishing
  authority domain outside Phase 7.
- Do not promise a Railway duration before benchmarking the configured service.
  Record queue, image-build, pytest, packaging, host-verification, and deployment
  times for at least three exact SHAs; then set an operational SLO from observed
  p95 rather than plan tier alone.
