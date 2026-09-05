# Railway stateful recovery (phase 6)

Phase 6 makes an activated Railway stateful platform recoverable without
creating a second writer plane. It combines three independent controls:

1. Railway volume backups with daily, weekly, and monthly retention plus one
   locked canary;
2. Railway PostgreSQL point-in-time recovery (PITR), the same three backup
   schedules, and a separately locked database canary; and
3. a daily backup-v4 export that is restored in isolation and then stored in
   external S3-compatible object storage under COMPLIANCE Object Lock.

Railway documents volume backups, PostgreSQL PITR, and portable logical dumps
as complementary recovery layers. See Railway's official
[volume backup](https://docs.railway.com/volumes/backups),
[PITR](https://docs.railway.com/volumes/point-in-time-recovery), and
[PostgreSQL backup and restore](https://docs.railway.com/guides/postgres-backups-restores)
guides.

This phase does not switch public traffic, grant writer authority, restore a
Railway backup in place, or start Hetzner services. Its portable restore is a
drill against an isolated runner and ephemeral PostgreSQL database.

## Implemented control plane

The production supervisor in `seiche.stateful_cutover` checks a root-promoted
closed command inbox. The FastAPI ingress accepts only a fresh, operation-scoped
Ed25519 command at the exact Railway origin after the edge token, host, release,
project, environment, service, deployment, and volume identities all match.
For one valid `recovery_export` command it:

1. keeps the production API online;
2. stops both Railway writer children;
3. takes an online SQLite copy, archives market/NBS and Palimpsest China
   activation state, dumps PostgreSQL, and commits the exact nine-file
   backup-v4 generation atomically;
4. restarts both writers and observes them alive; and
5. publishes a content-bound immutable recovery receipt.

An export failure restarts the writers but cannot publish a success receipt.
An interrupted run revalidates the already committed bundle and can seal the
same receipt after restart. Receipt names are sortable
`SNAPSHOT_ID-REQUEST_ID.json` values; no mutable `latest` pointer is trusted.

`.github/workflows/railway-stateful-recovery.yml` has four jobs across three
separate protected environments:

- `railway-stateful-recovery-admin` may configure native backup schedules and
  create/lock the two bootstrap canaries;
- `railway-stateful-recovery-monitor` has read-only monitoring credentials and
  proves backup freshness, PITR coverage, volume headroom, and agreement
  between the direct Railway origin and the public edge; and
- `railway-stateful-recovery-export` may request a portable export and write
  uniquely named, locked external objects. It cannot change edge or authority.

Scheduled jobs are inert until repository variable
`RAILWAY_STATEFUL_PHASE6_ENABLED` is exactly `true`.

## Protected environments and secrets

Require human reviewers on `railway-stateful-recovery-admin`: it changes native
backup schedules and creates/locks the bootstrap canaries. Keep the scheduled
`railway-stateful-recovery-monitor` and `railway-stateful-recovery-export`
environments restricted to `main`, but do **not** configure per-run required
reviewers on either one. Their six-hour monitor and daily append-only export
must start unattended so the workflow's 26-hour freshness bound remains
enforceable; a queued environment review is not recovery evidence. The export
lane can request a no-authority-change snapshot and append immutable evidence,
but it cannot cut over traffic, grant writers, restore production, delete an
object, or weaken retention.

This automation exception does not extend to any mutable cutover, activation,
writer-grant, reverse-transfer, or production-recovery environment. Those
environments remain manually dispatched and required-reviewer gated. Add these
secrets to the admin environment:

- `RAILWAY_TOKEN`
- `RAILWAY_PROJECT_ID`
- `RAILWAY_ENVIRONMENT_ID`
- `RAILWAY_STATEFUL_SERVICE_ID`
- `RAILWAY_STATEFUL_VOLUME_ID`
- `RAILWAY_POSTGRES_SERVICE_ID`

The monitor environment receives the same six values plus:

- `RAILWAY_STATEFUL_ORIGIN` (exact `https://NAME.up.railway.app`)
- `RAILWAY_EDGE_TOKEN`

The export environment receives all eight monitor values (including the origin
and edge token), plus these credentials for a bucket outside Railway:

- `SEICHE_OFFSITE_S3_ENDPOINT`
- `SEICHE_OFFSITE_S3_BUCKET`
- `SEICHE_OFFSITE_S3_PREFIX`
- `SEICHE_OFFSITE_S3_ACCESS_KEY_ID`
- `SEICHE_OFFSITE_S3_SECRET_ACCESS_KEY`
- `SEICHE_OFFSITE_S3_REGION`
- `SEICHE_OFFSITE_S3_SSE_C_KEY_B64` (canonical base64 for one separately
  retained 32-byte key)
- `SEICHE_RAILWAY_RECOVERY_SIGNING_KEY_PEM` (operation-scoped Ed25519 key for
  `recovery_export` and `offsite_acknowledgment` only)

The bucket must have versioning and a default COMPLIANCE Object Lock retention
of at least 30 days enabled. Its principal needs put, HEAD, and version-pinned
GET access, but no delete or retention-shortening permission. Hetzner Object
Storage supports SSE-C rather than AWS-managed SSE-S3, so every object uses the
separately retained 32-byte customer key. The signed upload includes
`Content-MD5` and immutable SHA-256 metadata. Acceptance requires the same
metadata, SSE-C key digest, version ID, at least 29 remaining retention days,
and a matching SHA-256 from an exact-version download. This avoids unsupported
AWS checksum/SSE-S3 headers without relaxing the recovery evidence contract.
Retain the same raw key in at least one protected operator recovery store
outside GitHub; an unreadable GitHub environment secret is not a recovery copy.
Never put the key in a receipt, log, command argument, Railway variable, or
Actions artifact.

Never put Railway, GitHub, Hetzner, off-site, or signing credentials in the
stateful service. The edge token is an ingress secret, not signing authority.
Phase 6 runtime export jobs have no Railway SSH, SFTP, SCP, or volume-file capability: they submit
signed commands over the exact HTTPS origin and reads canonical result
envelopes from exact-deployment logs. The origin's lowercase no-port host must
equal the runtime `RAILWAY_PUBLIC_DOMAIN`; the edge token is required on every
command and capability request.

## Pre-activation bootstrap

Do not set `RAILWAY_STATEFUL_PHASE6_ENABLED` yet.

1. Merge the reviewed Phase 6 SHA. Complete three distinct Phase 4 shadow
   restores and review their resource/timing evidence.
2. Manually dispatch `railway-stateful-recovery` on `main` with
   `operation=configure-native-backups` and
   `confirmation=ENABLE_NATIVE_BACKUPS_AND_LOCK_CANARIES`.
3. Wait for any PITR-enablement deployment to become healthy. Review the admin
   artifact: schedules and both returned lock results must be exact. The first
   live production monitor is necessarily post-activation because it requires
   a production activation receipt and matching public edge.
4. Manually dispatch `operation=preflight-offsite` with
   `confirmation=PROVE_EXTERNAL_OBJECT_LOCK_ONLY`. Review its non-production
   canary, signed `Content-MD5`, metadata and downloaded SHA-256 match, version
   IDs, encryption, COMPLIANCE retention, private artifact, and OIDC
   attestation.
5. Confirm the Phase 6 code, workflow policy, crash-resume, continuity-failure,
   and isolated restore tests are green on the exact Phase 6 candidate SHA.

These are the Phase 6 entry controls for Phase 5 activation. A real production
export cannot precede activation; it is bound to the activation receipt by
design.

## Immediate post-activation seal

After the Phase 5 writer grant succeeds, do not acknowledge activation on
Hetzner or enable schedules yet.

1. The successful activation workflow immediately dispatches
   `operation=export-recovery` with
   `confirmation=EXPORT_WITHOUT_AUTHORITY_CHANGE`. Its prerequisite monitor
   proves exact native schedules, fresh backups, both locked canaries, PITR
   coverage/archiver health, at least 20 percent volume headroom, and matching
   Railway-origin/public production identities. For an export operation only,
   the prerequisite monitor may tolerate a missing or broken *prior* portable
   receipt pair so the export can bootstrap or repair it. That exception never
   relaxes native backup, PITR, headroom, or production-identity checks, and the
   operation cannot succeed until its new receipt pair validates.
   Before sampling either API health body, the monitor waits up to fifteen
   minutes for the first production board. Only the documented HTTP 503
   warm-up states are retryable; other failures stop immediately. The samples
   taken after readiness must still pass the fifteen-minute freshness limit.
2. Review the private artifact and both production OIDC attestations. Confirm
   the activation-bound recovery receipt, uninterrupted API probe log, isolated
   reverse-restore proof, off-site receipt, every per-object SHA-256, version
   ID, COMPLIANCE retention, and the copied Railway off-site receipt.
3. Dispatch `operation=monitor` once more. It must now require fresh Railway
   recovery and off-site receipts rather than using the bootstrap exception.
4. Acknowledge Phase 5 activation on the frozen Hetzner host only after these
   proofs pass. Then set repository variable
   `RAILWAY_STATEFUL_PHASE6_ENABLED=true`.

The same explicit export dispatch is the recovery path when Railway accepted
activation but the Phase 5 job failed before its artifact or attestation was
retained. Do not rerun activation with a newly signed grant. The export
bootstrap selects the exact activation result from deployment-filtered logs;
after the first successful export, every recurring export instead derives the
activation-receipt digest from the newest fresh `recovery_offsite_paired`
result. This prevents scheduled recovery from depending on an old activation
line remaining inside the provider's log-retention window.

The monitor runs at minute 17 every six hours. A portable export runs daily at
02:31 UTC after the same monitor gate. Both schedules fail closed when a native
backup, portable receipt, off-site receipt, PITR probe, volume threshold, or
production identity is stale or invalid.

The portable receipts have independent, strict current contracts:

- `seiche.railway-recovery-export-receipt.v4`; and
- `seiche.railway-offsite-recovery-receipt.v3` (the unchanged off-site
  transport envelope).

No v3 export, candidate, or shadow receipt is parsed as current evidence, and
no v2 off-site receipt is accepted. Before pausing writers, the runtime
recovers the activation-bound v4 candidate and the exact v4 shadow
receipt named by that candidate. Shadow, candidate, live generation, exported
backup audit, isolated reverse restore, recovery receipt, and off-site receipt
must all carry the same closed `palimpsest_china_state` identity. Its fields are
exactly `audit_schema`, `tree_sha256`, `active_activation_id`, and
`pending_candidate_activation_id`; pending must be null. This equality is
required even when inactive, and an active ID makes the no-fallback boundary
explicit: no older bundle or prior activation may silently replace it.
The production monitor selects the newest fresh, same-request
`recovery_created` and `recovery_offsite_paired` evidence from filtered
exact-deployment logs and rejects digest, deployment, replica, release, schema,
or age drift. It never lists or downloads receipt directories.

The v4 recovery receipt also embeds a closed
`seiche.agent-room.restore-audit.v1` result. The exporter takes an online copy
of initialized Agent Room SQLite state, loads the restored operator key,
verifies its independent key-bound initialization seal, verifies every
participant key binding, and audits each room's signed genesis and event
hash/signature chain. A retained seal with a missing database, or a database
without its seal, fails as state loss. It then restores the exact portable bundle in
isolation and requires the restored audit to match before committing the
snapshot. Bundle-backed receipt validation repeats that isolated restore and
compares the Agent Room audit along with the NBS result and filesystem tree
digests. Never-initialized state is explicit; its audit retains an existing
operator-key ID as the only authorized later bootstrap identity, or records a
closed unprovisioned state when no key exists. Partial state, key mismatch, or
chain drift fails closed.

Recovery and reverse-restore receipts remain exact point-in-time proofs of the
complete API and market trees; mutable Agent Room bytes are not excluded and
their hashes are not replaced by a semantic-only backup digest. An activated
process restart is intentionally different: it binds the exact candidate,
grant, activation receipt, runtime paths, and key identity, keeps NBS and
Palimpsest byte hashes exact, then performs current SQLite, ownership, layout,
and Agent Room chain audits while allowing legitimate post-activation growth.

That local current-chain audit is tamper-evident, not rollback-proof. The
candidate receipt rejects truncation below its recorded baseline, but cannot
by itself distinguish a valid older state created after activation. Detecting
post-activation rollback requires a later independently retained, published,
or off-site Agent Room head checkpoint. A completed v4 recovery/off-site pair
provides historical evidence for its export time; startup does not claim that
the candidate baseline is the latest checkpoint.

Immediately after deploying the v4 consumer, dispatch one `export-recovery`
operation to establish a v4 recovery/v3 off-site pair. That operation
deliberately permits an absent prior proof, but does not accept a v3 recovery
export or v2 off-site proof. Scheduled and manual `monitor` operations remain
fail closed until the current mixed-version pair exists.

## Evidence contract

Each successful export produces:

- a canonical activation-bound
  `seiche.railway-recovery-export-request.v2` containing only the SHA-256 of a
  random 32-byte download bearer and a bounded expiry;
- the exact v4 source shadow and activation-bound candidate receipts;
- the immutable v4 Railway recovery receipt with its restored Agent Room audit;
- the exact nine-member backup-v4 generation, including the immutable
  Palimpsest China state archive and canonical audit receipt;
- a canonical reverse-restore proof containing NBS audit result, filesystem
  tree digests, the exact Palimpsest China state identity, and four PostgreSQL
  counts/floors;
- a canonical off-site receipt with each object's key, size, SHA-256, and
  version ID plus the same Palimpsest China state identity; and
- separate OIDC attestations for the Railway and off-site receipts.

After the runtime emits `recovery_created`, the job uses the unlogged bearer for
at most two hours to download a fixed 14-member allow-list from the exact
origin: five chain documents plus the nine-member backup-v4 generation. The
runtime stores only the bearer digest and streams only verified regular files.
The bearer is deleted before artifact creation. After Object Lock succeeds, the
job signs `offsite_acknowledgment`; acceptance requires a
`recovery_offsite_paired` result whose embedded recovery and off-site receipts
match the locally verified bytes exactly.

The root supervisor keeps each export command in a processing journal until
the recovery receipt and fixed evidence directory are durably validated. On a
crash it resumes the same request and snapshot, repairs exact immutable
evidence, and emits `reused`; after a seal-before-log crash, startup re-emits
the newest durable recovery/off-site pair. CI re-proves the exact current
replica during each poll and accepts submission- or restart-replica results
only when their evidence digests agree.

The large bundle is not uploaded as a GitHub Actions artifact. Locked external
objects are the durable portable copy; the 90-day private Actions artifact
contains only receipts, restore proof, and provider HEAD evidence.

To restore one exact object from a reviewed off-site receipt, create a
caller-owned mode-0600 environment file containing the helper names
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`,
`S3_ENDPOINT`, `S3_BUCKET`, and `S3_SSE_C_KEY_B64`. The protected GitHub job
maps `SEICHE_OFFSITE_S3_SSE_C_KEY_B64` to that last helper name. Then use the
receipt's immutable tuple without printing any credential:

```bash
test "$(stat -c '%a:%u' /secure/seiche-credentials/object-storage.env)" = \
  "600:$(id -u)"
set -a
. /secure/seiche-credentials/object-storage.env
set +a
install -d -m 0700 /secure/seiche-recovery
object=seiche.dump
key=$(jq -er --arg name "$object" '.objects[$name].key' offsite-receipt.json)
version=$(jq -er --arg name "$object" '.objects[$name].version_id' offsite-receipt.json)
sha256=$(jq -er --arg name "$object" '.objects[$name].sha256' offsite-receipt.json)
ops/deploy/seiche-s3-object-lock.sh get-verify \
  "$key" "$version" "$sha256" "/secure/seiche-recovery/$object"
```

The helper re-HEADs that exact non-null version with SSE-C, compares its locked
SHA-256 metadata, downloads only that version, checks the restored SHA-256,
fsyncs it, and publishes a new mode-0600 file into the caller-owned mode-0700
directory without overwriting an existing path.

## Failure handling

- If the export or bundle validation fails, verify both Railway writer children
  are healthy. No recovery receipt means the attempt did not succeed. Restart
  the stateful deployment to retry a deferred request only after reconciling
  the failure.
- If writers cannot restart, treat it as a Railway production incident. Do not
  unmask the old Hetzner units.
- If off-site upload or Object Lock verification fails, the Railway bundle and
  receipt remain evidence, but the recovery run is incomplete. Re-dispatch to
  a new request ID after fixing storage.
- If monitoring reports less than 20 percent volume headroom, stop new
  migrations/exports and increase or clean storage through a separately
  reviewed action. This workflow has no delete operation.
- If either public/origin header ceases to report the same production
  deployment and release SHA, stop recovery automation and investigate the
  authority discrepancy.

## Reverse transfer boundary

The daily proof establishes that the external backup-v4 bytes can rebuild the
filesystem, NBS chain, Palimpsest China bundles/receipts/markers, SQLite
database, and PostgreSQL tables on infrastructure outside the production
Railway databases. It is not permission to fail over.

A real reverse transfer to Hetzner requires a new reviewed controller and a new
authority receipt. The incident sequence must be:

1. establish that Railway writers are stopped or fenced, and record that proof;
2. select one locked off-site receipt and verify every downloaded object,
   activation binding, and OIDC attestation;
3. stage the exact recorded commit and restore the bundle into isolated
   Hetzner filesystem/PostgreSQL generations;
4. run strict API, NBS, SQLite, table-floor, freshness, and worker-idempotency
   checks while Hetzner remains non-authoritative;
5. move the public edge to a read-only Hetzner candidate and independently
   prove exact deployment/release identity; and
6. issue a separately protected one-time writer grant, then start Hetzner
   writers and seal the new authority receipt.

Never use Phase 5's pre-activation rollback after a Railway activation receipt
exists. Never start old frozen Hetzner units from their stale generation. A
reverse transfer that lacks a new fence, candidate proof, edge proof, writer
grant, and receipt is an unsafe dual-writer attempt.

## Remaining state domain

Phase 6 covers the API, market collectors, source worker, market/NBS files,
Palimpsest China activation state, SQLite, and PostgreSQL state moved by Phase
5. Phase 7 implements a separate
snapshot/restore, authority, delivery-idempotency, update-offset, native-backup,
and monitoring contract for `/var/lib/seiche-bot`; follow
`RAILWAY-TELEGRAM.md`. Until its activation receipt and first production
monitor exist, do not claim the Seiche bot workload has moved to Railway.

## Native PostgreSQL health probe authentication

The deployed PostgreSQL server uses major 18. The stateful image therefore
copies PostgreSQL 18 dump and restore tools from a digest-pinned Bookworm image;
the isolated reverse-restore job uses the same image. An older `pg_dump` cannot
export a newer server, even when an older dump restored successfully during
migration. Release CI runs a real dump/restore round-trip using the copied
runtime tools. Recovery targets must provide PostgreSQL 18 or a separately
validated newer major; restoring these exports into major 17 is not supported.

The native-backup admin and monitor environments additionally hold
`RAILWAY_RECOVERY_PROBE_SSH_KEY`. Railway's PITR status command probes
pgBackRest and `pg_stat_archiver` through SSH; an API project token alone
cannot authenticate those probes. The protected jobs resolve and verify the
PostgreSQL instance against the exact project, environment and service before
installing a transport wrapper. It accepts only that target and the two
checksum-bound read-only commands emitted by CLI 5.43.1. Host keys are pinned,
interactive authentication and user SSH configuration are disabled, and no
volume-file or arbitrary command transport is exposed by the wrapper.

Railway registers SSH keys at account/workspace level, not per service. Keep
this dedicated key only in the reviewed recovery environments and a protected
operator recovery store; registration is broader than the wrapper's allowed
target. Rotate or revoke it through the owning Railway account. It must never
be installed in the stateful application or included in evidence artifacts.
The final cutover and signed runtime export paths retain their HTTPS-only
transport.
