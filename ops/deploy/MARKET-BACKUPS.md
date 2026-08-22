# Seiche market-data backups

`install-market-platform.sh` provisions local snapshot and restore-check jobs,
plus an optional encrypted off-node copy job, for the canonical PostgreSQL-
backed market data plane. They do not collect data, run a model, publish
research, or grant execution authority.

Production filesystem evidence state and these snapshots are mount-guarded on
the pinned Hetzner Volume. Live PostgreSQL `PGDATA` and the compatibility/API
tree remain outside this phase. See [HETZNER-VOLUME.md](HETZNER-VOLUME.md) for
the exact scope, identity contract, fail-closed systemd ordering, and
operator-gated cutover sequence.

## Narrow funding-export access

The installer creates the system group `seiche-world-model-readers`. Members
can traverse `/var/lib/seiche` and `/var/lib/seiche/exports`, but cannot list
either directory. The group can read only the setgid directory:

```text
/var/lib/seiche/exports/us-usd-funding-core-v1
```

The Seiche writer remains the directory owner. Its atomic temporary files
inherit the reader group and are committed as mode `0640`. A consumer installer
may later add its dedicated, unprivileged account to the group. Do not add a
consumer to the broader `seiche` group: that group also reaches private Seiche
configuration and unrelated state.

The group name can be changed with
`SEICHE_FUNDING_EXPORT_READER_GROUP` during installation. No consumer account,
model package, signing key, or object-storage credential is created here; those
belong to the consuming repository.

## Daily snapshot

`seiche-market-backup.timer` runs at 02:00 UTC with up to ten minutes of jitter,
after the current funding export window and before the host's heavier 03:15 UTC
jobs. The service is low-priority and capped at half a CPU and 1 GiB RAM.

Every run:

1. asks the selected native PostgreSQL cluster for its live port instead of
   assuming `5432` or `5433`;
2. writes a custom-format `pg_dump` of database `seiche`;
3. archives `/var/lib/seiche` with numeric ownership, ACLs, and xattrs;
4. copies the API/legacy data directory and replaces its live SQLite files
   with a transactionally consistent online backup verified by
   `PRAGMA quick_check`;
5. records a pre-dump lower bound for all four append-only/upsert-only market
   tables and the deployed Git SHA;
6. validates all dump/archive catalogues, then writes research-only/no-
   authority metadata and a `SHA256SUMS` inventory;
7. verifies that inventory, flushes the staged filesystem, and atomically renames the
   staging directory to `/var/backups/seiche-market/<UTC stamp>`; and
8. only after that commit, removes timestamped local snapshots older than the
   configured retention (21 days by default).

A failed run removes its hidden staging directory and cannot replace a prior
snapshot. Snapshot files are root-only mode `0600`; directories are `0700`.

Operator checks:

```sh
systemctl list-timers seiche-market-backup.timer
systemctl start seiche-market-backup.service
journalctl -u seiche-market-backup.service -n 100 --no-pager
```

## Weekly restore check

`seiche-market-restore-check.timer` runs Sundays at 07:30 UTC with up to fifteen
minutes of jitter. It selects the newest committed snapshot, verifies every
checksum, extracts the state tar archive into a temporary directory under the
recovery-proof root, verifies the restored API SQLite database with
`PRAGMA quick_check`, and restores the dump into a uniquely named scratch
database. Temporary extraction happens inside that dedicated root-controlled
directory. Every restored critical-table count must meet or exceed the
recorded
pre-dump floor before the filesystem scratch trees and scratch database are
removed. This lets continuous ingestion proceed during `pg_dump` without
turning ordinary appends into false backup failures.

The production `seiche` database and live state tree are never restore targets.
A trap drops the scratch database after either success or failure. A successful
check atomically records its receipt at:

```text
/var/lib/seiche-recovery-proof/backup-restore-check.status
```

Both services share `/run/lock/seiche-market-backup.lock`, so a manual check
cannot overlap an active backup. Failures enter systemd's failed-unit state and
use the production node's existing failure-alert handler.

Operator checks:

```sh
systemctl start seiche-market-restore-check.service
journalctl -u seiche-market-restore-check.service -n 100 --no-pager
cat /var/lib/seiche-recovery-proof/backup-restore-check.status
```

## Continuous collection and data readiness

`seiche-source-worker.service` refreshes the broad legacy/engine source cache
every five minutes. Source-native TTLs still govern upstream requests, so the
poll loop does not bypass publisher limits. The `Type=notify` service becomes
ready only after its first durable source sweep. Its systemd watchdog proves
process liveness, while the repository heartbeat expires when acquisition can
no longer complete; these are deliberately different signals.

`seiche-data-readiness.timer` runs every five minutes and fails closed when:

- the API snapshot is older than 15 minutes, future-dated beyond five minutes
  of clock skew, or contains collector/critical faults;
- either collector heartbeat is missing or overdue;
- the newest backup is older than 36 hours or implausibly future-dated;
- the exact v2 restore receipt is missing, invalid, older than eight days, or
  future-dated;
- a required service/timer is inactive, or a required timer is disabled for
  the next boot; or
- block or inode use reaches 90 percent on a monitored filesystem.

On a fresh host or the first v2 rollout, installation does not enable the
readiness timer immediately. It starts the source worker, runs a real backup,
restores and checks that snapshot in isolation, executes readiness once without
requiring its not-yet-active timer, and enables the timer only after that proof
passes. Any failed stage leaves the timer disabled and the deployment nonzero.

Operator checks:

```sh
systemctl status seiche-source-worker.service seiche-data-readiness.timer
systemctl start seiche-data-readiness.service
journalctl -u seiche-data-readiness.service -n 100 --no-pager
```

## Durability boundary

The local snapshot and live filesystem evidence remain on one attached Volume.
That protects the host root disk from capacity pressure, but loss or corruption
of the Volume can still remove both. The optional off-node lane copies only a
completed, checksum-valid v2 snapshot to private Hetzner Object Storage.

`seiche-market-offsite-backup.service`:

1. holds an exclusive offsite-run lock, then shares
   `/run/lock/seiche-market-backup.lock` with the local producer and restore
   check while it selects the newest committed UTC snapshot; hidden stages,
   extra members, links, malformed manifests, or any checksum failure are
   rejected;
2. requires the snapshot's `deployed-sha.txt`, the application checkout, and
   `/var/lib/seiche-deploy/deployed-sha` to name the same 40-character commit,
   rejects a snapshot older than 36 hours, and does not upload a snapshot that
   the destination-bound `last_success` already proves;
3. packages the closed snapshot and encrypts it locally with GnuPG 2.4,
   AES-256, OCB authenticated encryption, and salted iterated SHA-512 S2K;
4. verifies that the destination bucket has exactly 90-day **COMPLIANCE**
   Object Lock before uploading to a deterministic first-canary path or a
   unique recurring-attempt path;
5. uses only `rclone copyto --immutable` operations and has no remote delete,
   sync, purge, overwrite, or retention-cleanup path;
6. captures each returned S3 VersionId and ETag, rejects anonymously readable
   objects, downloads the exact ciphertext version just uploaded, compares its
   SHA-256, authenticates and decrypts it into a private scratch directory,
   then verifies every restored source hash and the closed v2 manifest again;
   and
7. uploads `RECEIPT.json` last, captures and downloads that exact version
   byte-for-byte, and atomically commits root-only status at
   `/var/lib/seiche-offsite-backup/status.json`.

A remote attempt without `RECEIPT.json` is incomplete. Recovery must enumerate
receipt versions and use the recorded ciphertext VersionId and SHA-256, never
an unqualified latest-by-key object. Failed attempts may remain immutable until
an operator-managed lifecycle expires them; recovery tooling must ignore them.
A failure after the attempt workspace and status trap are established preserves
the last successful proof inside `last_success` while recording the current
failure. Earlier configuration/snapshot/disk preflight failures are visible in
the failed systemd unit and its OnFailure alert without replacing status.
Any prior `running` status, or `failed` status that reached receipt intent or a
receipt VersionId, is an unresolved commit boundary. Both the job and installer
refuse to re-arm recurring writes until an operator inspects the exact attempt
path/version history and atomically reconciles status; this prevents a crash
between remote receipt publication and local status fsync from duplicating the
same snapshot.
Scratch plaintext, ciphertext, and the
temporary curl credential disappear on every normal, failed, or signalled run.
After a hard kill or host crash, the next exclusively locked invocation removes
only strictly named, non-mounted stale run/status stages from the two dedicated
root-private directories before proceeding.

The work root defaults to `/var/cache/seiche-market-offsite-backup`. Before
encryption, the service requires four times the selected snapshot's byte size
plus 1 GiB free and refuses a snapshot over 25 GiB by default. These are
operator-adjustable safety ceilings, not retention settings.

### Root-only provisioning contract

The installer never creates a bucket, writes an object, generates a secret, or
copies a credential. It installs the service and timer but keeps the timer
disabled unless a completed canary has already been proven. Provision these
files directly on the host; never commit their values:

1. `/etc/seiche/offsite-backup.env`, exactly nine lines, root-owned mode
   `0600`;
2. `/etc/seiche/offsite-backup.passphrase`, exactly one newline-terminated
   32-4096 byte passphrase, root-owned mode `0400`, with a tested off-node
   escrow copy; and
3. the existing `/root/.config/anchor/object-storage.env`, root-owned mode
   `0600`. Seiche reuses its reviewed Hetzner S3 connection variables but uses
   a dedicated prefix. It never reads or reuses MyQuant's encryption
   passphrase.

The Seiche environment file has this exact non-secret shape:

```ini
SEICHE_OFFSITE_BACKUP_BUCKET=REVIEWED_PRIVATE_BUCKET
SEICHE_OFFSITE_BACKUP_PREFIX=seiche/market-backups/v1
SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE=anchor
SEICHE_OFFSITE_BACKUP_WRITE_ENABLED=1
SEICHE_OFFSITE_BACKUP_CANARY=1
SEICHE_OFFSITE_BACKUP_KEY_ID=market-key-2026-08-v1
SEICHE_OFFSITE_BACKUP_DESTINATION_ID=hetzner-primary-v1
SEICHE_OFFSITE_BACKUP_RETENTION_MODE=COMPLIANCE
SEICHE_OFFSITE_BACKUP_RETENTION_DAYS=90
```

The bucket must be private and created with Object Lock enabled; Object Lock
cannot be added after bucket creation. Its default rule must be exactly 90 days
in COMPLIANCE mode. If an operator adds lifecycle expiry, its age must be
greater than 90 days. Hetzner S3 keys are project-wide rather than bucket-
scoped, so the recovery project and membership remain part of the threat
boundary. See Hetzner's [Object Lock guide](https://docs.hetzner.com/storage/object-storage/howto-protect-objects/protect-object-lock-retention/)
and [S3 credential scope](https://docs.hetzner.com/storage/object-storage/faq/s3-credentials/).

`KEY_ID` is a non-secret identifier for one immutable passphrase generation;
keep its mapping to the escrowed passphrase in the recovery runbook.
`DESTINATION_ID` is a non-secret operator name for the reviewed Hetzner
account/project endpoint. Receipts bind both IDs plus endpoint, region, bucket,
and prefix. Passphrase, key-ID, endpoint, or project rotation requires a new
dedicated prefix and a new canary; never reuse an old key ID for new secret
bytes. The job itself never changes lifecycle rules or deletes any version, so
capacity and any post-retention lifecycle are separate operator-owned policy.
Scheduled enablement therefore requires external bucket-capacity monitoring
that counts current versions, noncurrent versions, and incomplete multipart
uploads and alerts before quota exhaustion. If deletion is separately approved,
the reviewed lifecycle must expire current and noncurrent versions only after
the 90-day COMPLIANCE window and abort stale multipart uploads; it is never
installed or changed by this copy-only job.

### Controlled first write and recurring schedule

COMPLIANCE writes are intentionally irreversible during their retention
window. The deterministic `PREFIX/canary/v1` namespace is accepted only when a
signed `ListObjectVersions` probe proves it has no versions or delete markers
and authenticated HEAD proves all three keys absent. Keep
`SEICHE_OFFSITE_BACKUP_CANARY=1`, run the installer, and confirm the timer is
disabled. Then perform exactly one manual end-to-end write:

```sh
systemctl start seiche-market-offsite-backup.service
systemctl status seiche-market-offsite-backup.service --no-pager
cat /var/lib/seiche-offsite-backup/status.json
```

Success requires `status=success`, `restore_verified=true`, the expected key and
destination IDs, matching source/ciphertext hashes, exact object VersionIds, a
receipt key/version, private-read evidence, and 90-day COMPLIANCE evidence. The
script refuses another canary write after that success. A kill or local status
failure after any canary object is written also blocks automatic retry because
the remote version history is no longer empty; reconcile and verify those
versions manually, then choose a new prefix/canary rather than overwriting.
Change only `SEICHE_OFFSITE_BACKUP_CANARY=0` using an atomic root-only file
replacement, rerun `install-market-platform.sh`, and verify timer enablement.
The installer will reject scheduled mode if the canary receipt does not match
the configured key ID, destination ID, bucket, prefix, and versioned-object
contract.

`seiche-market-offsite-backup.timer` runs at 05:20 UTC with stable jitter up to
20 minutes. That is separated from Seiche's 02:00 local snapshot, MyQuant's
03:04 offsite job, and Seiche's 03:15 validation. The deployment controller
stops and snapshots both offsite unit files and timer state before checkout
mutation. It starts a previously active timer only after exact-SHA health and
promotion, and restores prior bytes, absence, enablement, and activity on a
rollback.

The five-minute data-readiness monitor treats an absent configuration as
unconfigured and a valid `CANARY=1` configuration as manual bootstrap. Once
`CANARY=0` arms the daily schedule, readiness requires a root-private,
destination-bound `last_success` with exact object versions, 90-day COMPLIANCE
evidence, and a restore verification no older than 36 hours. A stopped timer or
a job that never starts therefore becomes an alert when the last recoverable
proof crosses that bound, without creating a circular dependency between
candidate readiness and post-promotion timer activation. Root-owned release
preflights skip only this freshness check so a stale proof cannot prevent
deployment of its own repair; the persistent monitor never sets that bypass.

This is off-node protection, not provider or account independence. A separate
provider/account copy and a downtime recovery exercise into explicitly chosen
non-production paths remain the stronger disaster-recovery boundary.
