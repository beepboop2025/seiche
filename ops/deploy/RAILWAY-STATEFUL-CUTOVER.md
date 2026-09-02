# Railway stateful cutover (phase 5)

Phase 5 moves authority for the Seiche API, market collectors, and source
worker from Hetzner to one Railway stateful service. It never uses dual writes.
The transition has three explicit states:

1. Hetzner is the sole writer and Railway has no production authority.
2. Hetzner is fully fenced; Railway serves authenticated, read-only candidate
   traffic; neither side writes.
3. A protected activation grant starts Railway writers; Hetzner remains
   runtime-masked and can no longer resume without a reverse transfer.

The files implementing that contract are:

- `seiche-railway-cutover-fence.sh`: host writer fence, final snapshot,
  pre-activation rollback, and post-activation acknowledgement;
- `seiche-railway-edge-mode.sh`: exact Caddy origin switch with a
  root-only ingress token;
- `seiche.stateful_cutover`: candidate restore, immutable receipts,
  grant validation, and supervised worker/API transition; and
- `.github/workflows/railway-stateful-cutover.yml`: separately
  protected candidate and activation dispatches.

This phase does not migrate `/var/lib/seiche-bot` or its Telegram timers.
Phase 7 implements that separate state and delivery-authority transfer in
`RAILWAY-TELEGRAM.md`; it must be activated and receipted independently before
anyone may claim the Seiche bot workload has moved.

## Mandatory entry gates

Do not start the maintenance freeze until all of these are true:

- the exact target SHA is signed, deployed, strictly healthy, recovery-sealed,
  and installed with the two root-owned helpers under
  `/etc/seiche/libexec`;
- three distinct Phase 4 shadow restores have green immutable receipts,
  restart/reuse checks, private health probes, artifacts, and attestations;
- their queue/build/restore timings and Railway CPU, memory, volume, and
  PostgreSQL peaks have been reviewed;
- the stateful service still has exactly one replica, one volume mounted at
  `/var/lib/seiche-platform`, and one Railway-private PostgreSQL
  reference;
- Phase 5 restart continuity and Phase 6 control tests, native-backup
  bootstrap, and external Object-Lock preflight are green according to
  `RAILWAY-STATEFUL-RECOVERY.md`; and
- a maintenance window, rollback operator, and exact authority confirmations
  are recorded.

Phase 5 code being merged is not an activation approval.

## One-time Railway and GitHub setup

Generate exactly one Railway-provided HTTPS domain for
`seiche-stateful-core`. Do not point DNS at it. Unauthenticated
requests receive 404 from the application; Caddy is the only intended client.
Retain one replica because a Railway volume cannot be mounted by overlapping
replicas.

Generate a 64-hex-character edge token in the approved secret manager:

```bash
openssl rand -hex 32
```

Create two GitHub environments with required reviewers:

- `railway-stateful-cutover-candidate`
- `railway-stateful-activation`

Add the following secrets to both:

- `RAILWAY_TOKEN`
- `RAILWAY_PROJECT_ID`
- `RAILWAY_ENVIRONMENT_ID`
- `RAILWAY_STATEFUL_SERVICE_ID`
- `RAILWAY_STATEFUL_VOLUME_ID`
- `RAILWAY_STATEFUL_ORIGIN` (exact
  `https://NAME.up.railway.app`)
- `RAILWAY_EDGE_TOKEN`

The Railway service itself receives only its private `DATABASE_URL`,
the exact volume identity, snapshot/fence identities, and the edge token. It
must not receive Hetzner credentials, an NBS signing key, Telegram credentials,
GitHub credentials, or a release-control token.

## 1. Deploy and inspect the exact host controls

Deploy the reviewed SHA to Hetzner through the existing signed release
controller. Before freezing anything:

```bash
test "$(cat /var/lib/seiche-deploy/deployed-sha)" = REVIEWED_SHA
test -x /etc/seiche/libexec/seiche-railway-cutover-fence.sh
test -x /etc/seiche/libexec/seiche-railway-edge-mode.sh
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

The Caddyfile keeps its loopback default until the root-only Railway edge
environment exists. The private world-model delivery and all non-Seiche
products on `api.seiche.info` remain local.

## 2. Freeze Hetzner and create the final snapshot

Run from a root shell with no inherited application environment:

```bash
/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  /etc/seiche/libexec/seiche-railway-cutover-fence.sh \
  prepare REVIEWED_SHA
```

The controller records unit prestate, stops/disables/runtime-masks the API,
collectors, release controllers, alert evaluator, and historical updater/API
names, creates exactly one new backup-v4 snapshot, runs its isolated restore
proof, and writes:

```text
/var/lib/seiche-railway-cutover/AUTHORITY-FENCE.json
```

The fence expires after four hours. Its canonical digest binds the exact
commit/tree, final snapshot inventory/content set, restore proof, release and
recovery receipts, latest shadow receipt, and complete inactive/masked unit
set.

From this point until rollback or activation, the public Seiche API has a
maintenance outage. Do not start any old unit manually.

## 3. Stage immutable final bytes

Using the existing reviewed operator channel, copy the final snapshot's nine
files and the canonical fence to a private workstation. Verify the snapshot
file set and both digests before upload.

Upload each snapshot member without replacement. Railway CLI v5.43.1 requires
the reviewed project, environment, and service context before `files`, and the
volume selector before the `upload` operation:

```bash
for member in seiche.dump var-lib-seiche.tgz palimpsest-china.tgz \
  palimpsest-china-state.json api-data.tgz table-counts.txt deployed-sha.txt \
  manifest.env SHA256SUMS; do
  railway volume --project REVIEWED_PROJECT_ID \
    --environment REVIEWED_ENVIRONMENT_ID \
    --service REVIEWED_SERVICE_ID files --volume REVIEWED_VOLUME_ID upload \
    "PRIVATE_FINAL_SNAPSHOT/$member" \
    "/inbox/FINAL_SNAPSHOT_ID/$member"
done

railway volume --project REVIEWED_PROJECT_ID \
  --environment REVIEWED_ENVIRONMENT_ID \
  --service REVIEWED_SERVICE_ID files --volume REVIEWED_VOLUME_ID upload \
  PRIVATE_AUTHORITY_FENCE.json \
  "/authority-fences/AUTHORITY_FENCE_SHA256.json"
```

Never overwrite an inbox, fence, generation, database, grant, or receipt. A
partial attempt is a reconciliation event, not permission to delete evidence.

## 4. Restore the read-only candidate

Dispatch `railway-stateful-cutover` on the exact main SHA with:

- `operation=candidate`
- `snapshot_id=FINAL_SNAPSHOT_ID`
- `authority_fence_sha256=AUTHORITY_FENCE_SHA256`
- `confirmation=HETZNER_FROZEN_RAILWAY_READ_ONLY`

The candidate job proves the service, volume, sole Railway domain, PostgreSQL
reference, closed final snapshot, canonical fence, exact source archive/bundle,
restored filesystem/database generation, table floors, and direct authenticated
origin response. It starts only an unprivileged API. The pre-activation API
does not run the board warm/rebuild loop or initialize Agent Room state.
Mutation methods return 503, and requests without the token return 404.
Readiness requires the exact same-release active PostgreSQL handoff restored
into memory; it never repairs a missing handoff by writing candidate state.

The candidate receipt is exactly
`seiche.railway-cutover-candidate-receipt.v4`; v3 is not a fallback. Its closed
`palimpsest_china_state` object records the audit schema, semantic tree digest,
active activation ID, and null pending candidate ID from the final backup-v4
audit. The candidate request also exposes the exact source shadow-receipt
SHA-256. The workflow must recover that one canonical v4 shadow receipt and
require its four-field state identity to equal the final candidate before any
cutover proof is accepted. Candidate restart/reuse independently re-audits the
restored tree with the Railway uid/gid 10001 reader and refuses any tree or
activation-ID drift.

The v4 candidate also carries the closed restored Agent Room audit from its
source v4 shadow receipt. Candidate creation loads the restored operator key,
verifies the independent key-bound initialization seal, every participant key
binding, and each room's signed genesis and event hash/signature chain, and
requires the result to equal the source shadow audit exactly. Restart/reuse
repeats that audit, so missing database/seal state, restored-key, server-key ID,
count, state-digest, or signed-chain drift fails closed. A never-initialized
Agent Room remains an explicit `absent_uninitialized` result only when both the
database and seal are absent, rather than an omitted proof. An existing
operator key is bound into that absent result and is the only permitted later
bootstrap identity. If it is absent too, the candidate emits an explicit
`unprovisioned` runtime gate and Agent Room stays unavailable for this release.
Production activation inherits and revalidates the exact candidate receipt and
key binding; changing only the runtime environment cannot authorize a
replacement key or store. After the candidate API stops and before the first
Railway writer starts, the supervisor repeats the exact four-tree digest,
Agent Room audit, and PostgreSQL count equality checks. Any mutation during the
read-only serving window therefore aborts activation.

Record from the green artifact and attestation:

- request ID;
- exact Railway deployment UUID; and
- candidate receipt SHA-256.
- exact source shadow-receipt SHA-256 and the shared Palimpsest China state
  identity.

## 5. Switch the public edge while both writer planes are fenced

Run the host controller with the exact Railway origin and the same edge token
from the secret manager:

```bash
/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  SEICHE_EDGE_CONFIRM=RAILWAY_CANDIDATE_RECEIPTED_READ_ONLY \
  SEICHE_RAILWAY_ORIGIN=https://NAME.up.railway.app \
  SEICHE_RAILWAY_EDGE_TOKEN=REVIEWED_SECRET \
  /etc/seiche/libexec/seiche-railway-edge-mode.sh \
  railway REVIEWED_SHA CANDIDATE_DEPLOYMENT_UUID
```

The controller first probes the origin with the token, validates the Caddyfile,
writes a root-only environment and systemd drop-in, restarts Caddy, then probes
`https://api.seiche.info/api/health` without a token. Both responses
must report `candidate`, the exact deployment UUID, and exact release
SHA. Only the token digest enters the edge receipt.

At this point public reads use Railway, while POST/MCP mutation traffic receives
the bounded maintenance response. There is still no writer.

## Pre-activation rollback

Rollback is permitted only after the Railway candidate is stopped and an
operator has proved no Railway writer or activation receipt exists.

First restart the exact Hetzner prestate:

```bash
/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  SEICHE_CUTOVER_ROLLBACK_CONFIRM=RAILWAY_CANDIDATE_STOPPED_NO_WRITERS \
  /etc/seiche/libexec/seiche-railway-cutover-fence.sh \
  rollback REVIEWED_SHA
```

Then return Caddy to loopback:

```bash
/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  SEICHE_EDGE_CONFIRM=RAILWAY_CANDIDATE_STOPPED_NO_WRITERS \
  /etc/seiche/libexec/seiche-railway-edge-mode.sh local
```

Verify strict public health and record both rollback receipts. Do not use this
path after a Railway activation receipt exists.

## 6. Activate Railway

Dispatch the same workflow on the exact SHA with:

- `operation=activate`
- `request_id=ACCEPTED_REQUEST_ID`
- `candidate_receipt_sha256=ACCEPTED_CANDIDATE_RECEIPT_SHA256`
- `deployment_id=EXACT_CANDIDATE_DEPLOYMENT_UUID`
- `confirmation=PUBLIC_EDGE_PROVES_CANDIDATE_ACTIVATE_RAILWAY`

The separately protected job recovers the candidate chain from the active
container and volume, directly probes the Railway origin, independently probes
the public edge, commits an immutable public-probe document and activation
grant, then waits for the same deployment to report `production`.
The supervisor starts both workers, observes them alive, writes the immutable
activation receipt, and only then admits the production API.

Any failure before the grant may use the pre-activation rollback. Any failure
after the grant is a Railway recovery incident; never restart Hetzner from its
old frozen state.

Before activation, Phase 6 must already have one accepted native-backup setup,
one non-production external Object-Lock preflight, and green recovery contract
tests. A production monitor/export cannot exist before the activation receipt
that binds it. Merely merging the Phase 6 workflow is not a green recovery
control.

Immediately after the grant, run Phase 6's first `export-recovery` operation.
Its prerequisite monitor, uninterrupted API log, isolated reverse restore,
external COMPLIANCE objects, Railway/off-site receipts, and both production
OIDC attestations must pass before the Hetzner acknowledgement in step 7. A
failure is a Railway recovery incident; it is never permission to restart the
old host.

## 7. Acknowledge activation on Hetzner

Download the attested canonical activation receipt to a root-readable local
path on Hetzner and independently verify its SHA-256. Then:

```bash
/usr/bin/env -i HOME=/root LANG=C LC_ALL=C PATH=/usr/bin:/bin \
  /etc/seiche/libexec/seiche-railway-cutover-fence.sh \
  finalize REVIEWED_SHA /root/activation-receipt.json \
  ACTIVATION_RECEIPT_SHA256
```

The resulting `activation-ack.json` permanently prevents the old host
rollback command. Keep all old units masked. Recovering to Hetzner now requires
Phase 6's reverse snapshot, isolated restore, public edge proof, and a new
authority transfer.

## Completion evidence

Phase 5 is complete only when the private evidence pack contains:

- signed exact-SHA Hetzner release and recovery receipts;
- final snapshot, isolated restore receipt, and authority fence;
- candidate request/receipt, direct-origin proof, artifact, and attestation;
- root edge receipt proving the exact public candidate;
- public candidate probe and activation grant;
- production activation receipt, public/origin production probes, artifact,
  and attestation; and
- Hetzner activation acknowledgement.

Railway deployment `SUCCESS`, health status alone, or a Caddy switch
alone is not authority proof.
