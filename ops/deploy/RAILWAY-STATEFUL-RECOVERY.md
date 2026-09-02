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

The production supervisor in `seiche.stateful_cutover` checks a closed recovery
request inbox. For one valid request it:

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

The bucket must have versioning and Object Lock enabled. Its IAM principal must
be able to put and HEAD objects with COMPLIANCE retention, but it does not need
delete or retention-shortening permissions. The workflow requires explicit
AES-256 server-side encryption and at least 29 remaining days when it verifies
each object.

Never put Railway, GitHub, Hetzner, edge, or off-site credentials in the
stateful service. The service receives only the canonical export request over
authenticated Railway SSH.

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
   canary, provider checksum, version IDs, encryption, COMPLIANCE retention,
   private artifact, and OIDC attestation.
5. Confirm the Phase 6 code, workflow policy, crash-resume, continuity-failure,
   and isolated restore tests are green on the exact Phase 6 candidate SHA.

These are the Phase 6 entry controls for Phase 5 activation. A real production
export cannot precede activation; it is bound to the activation receipt by
design.

## Immediate post-activation seal

After the Phase 5 writer grant succeeds, do not acknowledge activation on
Hetzner or enable schedules yet.

1. Immediately dispatch `operation=export-recovery` with
   `confirmation=EXPORT_WITHOUT_AUTHORITY_CHANGE`. Its prerequisite monitor
   proves exact native schedules, fresh backups, both locked canaries, PITR
   coverage/archiver health, at least 20 percent volume headroom, and matching
   Railway-origin/public production identities. For an export operation only,
   the prerequisite monitor may tolerate a missing or broken *prior* portable
   receipt pair so the export can bootstrap or repair it. That exception never
   relaxes native backup, PITR, headroom, or production-identity checks, and the
   operation cannot succeed until its new receipt pair validates.
2. Review the private artifact and both production OIDC attestations. Confirm
   the activation-bound recovery receipt, uninterrupted API probe log, isolated
   reverse-restore proof, off-site receipt, every per-object SHA-256, version
   ID, COMPLIANCE retention, and the copied Railway off-site receipt.
3. Dispatch `operation=monitor` once more. It must now require fresh Railway
   recovery and off-site receipts rather than using the bootstrap exception.
4. Acknowledge Phase 5 activation on the frozen Hetzner host only after these
   proofs pass. Then set repository variable
   `RAILWAY_STATEFUL_PHASE6_ENABLED=true`.

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
The production monitor downloads the bound shadow and candidate receipts again
and rejects any schema downgrade or state mismatch before accepting the latest
Railway/off-site pair.

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

- a canonical activation-bound request;
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

The large bundle is not uploaded as a GitHub Actions artifact. Locked external
objects are the durable portable copy; the 90-day private Actions artifact
contains only receipts, restore proof, and provider HEAD evidence.

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
