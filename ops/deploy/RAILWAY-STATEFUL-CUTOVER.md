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
- `seiche.stateful_control`: Ed25519 command validation, replay-safe runtime
  inboxes, and exact-deployment result envelopes; and
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

Add `SEICHE_RAILWAY_ACTIVATION_SIGNING_KEY_PEM` only to
`railway-stateful-activation`. Its public key and operation-scoped key ID are
pinned in `governance/railway-control-signers.json`; the private key must never
become a Railway variable, artifact, log, or candidate-environment secret.

The Railway service itself receives only its private `DATABASE_URL`, the exact
volume identity, snapshot/fence identities, `SEICHE_RAILWAY_CONTROL_ENABLED=1`,
and the edge token. It
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

This is the single operator-only SFTP handoff at the end of Phase 4, before
Phase 5 CI begins. Project-token CI cannot open Railway SSH or manipulate
volume files. Resolve the exact
Railway-provided SFTP host/port/user for the reviewed service instance, pin its
host key, and use a short-lived, operation-specific key. First prove both final
destinations are absent. Upload into a new random staging directory, verify all
local hashes, then promote the nine snapshot members with `SHA256SUMS` last and
the canonical fence last of all:

```bash
test -f /secure/railway-sftp-key
test "$(stat -f '%Lp' /secure/railway-sftp-key)" = 600
ssh-keygen -F REVIEWED_SFTP_HOST -f /secure/railway-known-hosts >/dev/null
sftp -b /secure/verified-upload.batch \
  -i /secure/railway-sftp-key \
  -o BatchMode=yes -o IdentitiesOnly=yes \
  -o UserKnownHostsFile=/secure/railway-known-hosts \
  -P REVIEWED_SFTP_PORT REVIEWED_SFTP_USER@REVIEWED_SFTP_HOST
```

The reviewed batch must contain only `mkdir`, `put`, `ls`, and final `rename`
operations for the random staging directory, `/inbox/FINAL_SNAPSHOT_ID`, and
`/authority-fences/AUTHORITY_FENCE_SHA256.json`. It must not contain `rm`, a
wildcard, a mutable `latest` path, or a destination outside the mounted volume.
Re-read and hash every promoted regular file through the same channel, then
delete the temporary private key locally. The Phase 4 handoff is then closed:
Phase 5 CI performs no SSH, SFTP, SCP, or Railway volume-file operation.

Never overwrite an inbox, fence, generation, database, grant, or receipt. A
partial attempt is a reconciliation event, not permission to delete evidence.

## 4. Restore the read-only candidate

Dispatch `railway-stateful-cutover` on the exact reviewed main workflow SHA with:

- `source_commit=SIGNED_RELEASE_SHA` (omit only when application and workflow
  commits are identical)

- `operation=candidate`
- `snapshot_id=FINAL_SNAPSHOT_ID`
- `authority_fence_sha256=AUTHORITY_FENCE_SHA256`
- `authority_fence_base64=CANONICAL_FENCE_ONE_LINE_BASE64`
- `confirmation=HETZNER_FROZEN_RAILWAY_READ_ONLY`

The candidate job validates the supplied canonical fence bytes against their
digest, proves the service, volume, sole Railway domain, PostgreSQL reference,
closed final snapshot, exact source archive/bundle,
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

The runtime emits the candidate receipt only through a canonical
`SEICHE_RAILWAY_STATEFUL_RESULT_V1` envelope in the exact deployment log. The
workflow pins the running replica, validates the created envelope, restarts the
same deployment, and requires a second replica to emit byte-identical `reused`
evidence. The receipt is never read from the volume by CI.

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
It also proves that the public host returns an exact 404 for both private
Railway control route families; those handlers never import
`seiche_stateful_upstream`.

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

Dispatch the same workflow on the same reviewed main workflow SHA, with the
same `source_commit` used for the candidate, and:

- `operation=activate`
- `request_id=ACCEPTED_REQUEST_ID`
- `candidate_receipt_sha256=ACCEPTED_CANDIDATE_RECEIPT_SHA256`
- `deployment_id=EXACT_CANDIDATE_DEPLOYMENT_UUID`
- `candidate_run_id=ATTESTED_CANDIDATE_GITHUB_RUN_ID`
- `confirmation=PUBLIC_EDGE_PROVES_CANDIDATE_ACTIVATE_RAILWAY`

The separately protected job downloads a closed candidate artifact, rejects
symlinks or extra members, and verifies its GitHub OIDC attestation against the
exact repository, workflow, main ref, and workflow SHA. Both operations
independently authenticate the application commit's SSH signature and require
it to be an ancestor of that workflow commit. The source archive, fence,
shadow, runtime headers, candidate receipt, and activation grant all bind that
application commit. This permits reviewed orchestration repairs without
changing a frozen release or pretending that its workflow bytes changed.
The two identities appear separately in the run summary. The application
commit is not used as an OIDC signer digest when the workflow differs.
It directly probes the Railway
origin and public edge, builds the immutable public probe and activation grant,
then signs a short-lived `activation` command with the protected operation key.
The command binds the exact project, environment, service, deployment, volume,
request, payload digest, nonce, and release SHA. It is posted over HTTPS only to
the exact Railway origin with the edge ingress token; its no-port lowercase
host must equal the runtime `RAILWAY_PUBLIC_DOMAIN`. The supervisor emits the
activation receipt through the exact-deployment log; CI has no interactive
shell or volume-file authority.
The runtime atomically claims the command into its root-owned processing
journal before publishing authority files and archives it only after those
immutable files validate. A crash resumes the same signed bytes without
extending their operation scope. CI separately proves the submission replica
and the current single running replica, so a restart result is admissible only
when its evidence is byte-identical to any pre-restart result.
The supervisor starts both workers, observes them alive, writes the immutable
activation receipt, and only then admits the production API.

Any failure before the grant may use the pre-activation rollback. Any failure
after the grant is a Railway recovery incident; never restart Hetzner from its
old frozen state.

If the signed activation command was accepted and Railway reports production
but the workflow later failed while collecting logs, uploading, or attesting
evidence, **do not create or submit a new activation grant**. Dispatch Phase
6 with `operation=export-recovery` and
`confirmation=EXPORT_WITHOUT_AUTHORITY_CHANGE`. Its bootstrap path reads only
filtered `SEICHE_RAILWAY_STATEFUL_RESULT_V1` records for the exact deployment,
request, release, and current replica, recovers the durable activation-receipt
digest, then downloads and validates the full receipt as part of the fixed
recovery chain. Complete the recovery/off-site pair and use those immutable
bytes for the missing activation evidence. A byte-identical command replay is
safe only while the original canonical command bytes are available; a fresh
grant is never a retry mechanism after authority has changed.

Before activation, Phase 6 must already have one accepted native-backup setup,
one non-production external Object-Lock preflight, and green recovery contract
tests. A production monitor/export cannot exist before the activation receipt
that binds it. Merely merging the Phase 6 workflow is not a green recovery
control.

Immediately after the activation receipt is attested, the workflow dispatches
Phase 6's first `export-recovery` operation automatically.
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
