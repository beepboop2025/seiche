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
3. archives `/var/lib/seiche` and `/var/lib/seiche-nbs` with numeric ownership,
   ACLs, and xattrs;
4. audits the separate root-controlled
   `/var/lib/seiche-palimpsest-china` tree through the exact signed-release
   launcher, archives it, normalizes and audits a scratch extraction, and
   rejects any pre/archive/post audit disagreement;
5. copies the API/legacy data directory and replaces its live SQLite files
   with a transactionally consistent online backup verified by
   `PRAGMA quick_check`;
6. records a pre-dump lower bound for all four append-only/upsert-only market
   tables and the deployed Git SHA;
7. validates all dump/archive catalogues, then writes research-only/no-
   authority metadata and a `SHA256SUMS` inventory;
8. verifies that inventory, flushes the staged filesystem, and atomically renames the
   staging directory to `/var/backups/seiche-market/<UTC stamp>`; and
9. only after that commit, removes timestamped local snapshots older than the
   configured retention (21 days by default).

A failed run removes its hidden staging directory and cannot replace a prior
snapshot. Snapshot files are root-only mode `0600`; directories are `0700`.

An activation durability run supplies a canonical, root-only request under
`/run/seiche-deploy`. That request fixes the new snapshot name, deployed release
SHA, live Palimpsest China activation ID, and canonical state-tree digest. A
conflicting manual snapshot override is rejected. The backup re-audits the live
tree while holding its normal market lease and will not commit a snapshot whose
audit differs from the request. The request does not grant backup, restore, or
offsite code permission to acquire the deploy or activation transaction locks.

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
database. It normalizes the intentionally stripped extraction modes on the
isolated NBS recovery tree, verifies the exact restricted object/export
structure, and runs the deployed strict loader over the matching public signed
revision chain. An empty restricted/public store is recorded as
`not_onboarded`; one fully verified matching head is recorded as
`verified_head`. Any malformed member, invalid signature, missing predecessor,
fork, restricted/public mismatch, or head mismatch fails the restore check.
For a v4 snapshot it also extracts the sibling Palimpsest China archive,
restores its exact root/group ownership and modes, reparses every immutable
receipt/marker and all eleven files in every retained bundle, recomputes bundle
and whole-tree identities, and requires the result to equal the canonical
snapshot audit. A legacy v3 snapshot can produce only an explicitly empty,
inactive Palimpsest China result.
Temporary extraction happens inside that dedicated root-controlled directory.
Every restored critical-table count must meet or exceed the recorded pre-dump
floor before the filesystem scratch trees and scratch database are removed.
This lets continuous ingestion proceed during `pg_dump` without turning
ordinary appends into false backup failures.

The production `seiche` database and live state tree are never restore targets.
A trap drops the scratch database after either success or failure. A successful
check atomically records its v5 receipt at:

```text
/var/lib/seiche-recovery-proof/backup-restore-check.status
```

Both services and the root-only NBS signed-export intake launcher share
`/run/lock/seiche-market-backup.lock`, so a manual restore check, snapshot, or
evidence commit cannot overlap either of the others. The launcher validates the
existing lock as a root-owned, root-group, mode `0600`, single-link regular file
and waits at most 300 seconds. It holds that outer lock across fixed-path storage
preflight, the complete NBS commit, and strict postflight; the NBS store's own
lock remains the inner revision-chain serializer. Backup/restore failures enter
systemd's failed-unit state and use the production node's existing
failure-alert handler.

The backup and restore one-shots also share the release controller's
`/run/seiche-deploy/deploy.lock` while invoking the sealed Palimpsest China
audit launcher. Their root-only `RuntimeDirectory=seiche-deploy` survives
one-shot exit and is recreated after boot. If the lock file itself was lost at
boot, the launcher creates it with no-follow/exclusive semantics, fsyncs the
new file and directory, validates root ownership, mode `0600`, link count and
inode identity, then acquires it. A concurrent safe creator is reopened and
validated; an unsafe replacement fails closed.

Operator checks:

```sh
systemctl start seiche-market-restore-check.service
journalctl -u seiche-market-restore-check.service -n 100 --no-pager
cat /var/lib/seiche-recovery-proof/backup-restore-check.status
sudo /etc/seiche/libexec/seiche-nbs-intake.py --help
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
- the exact v5 restore receipt is missing, invalid, older than eight days,
  future-dated, or does not record a strictly verified NBS full-store and
  Palimpsest China activation-state archive;
- the live Palimpsest China marker is provisional without an exact immutable
  durability receipt, or the restore-v5/offsite-v4 activation ID, canonical
  tree digest, snapshot, receipt digest, or scheduled mode differs from that
  receipt (a newer-looking inactive or older-activation snapshot still fails);
- a required service/timer is inactive, or a required timer is disabled for
  the next boot; or
- block or inode use reaches 90 percent on a monitored filesystem.

On a fresh host or the first v5 receipt rollout, installation does not enable the
readiness timer immediately. It starts the source worker, runs a real backup,
restores and checks that snapshot in isolation, executes readiness once without
requiring its not-yet-active timer, and enables the timer only after that proof
passes. Any failed stage leaves the timer disabled and the deployment nonzero.

Restore receipt v5 is intentionally not backward compatible with v4. Version 4
validated the full NBS restricted/public recovery store but did not bind the
separate Palimpsest China activation-state archive. During the v5 receipt
rollout the installer must produce a v4 snapshot and successful isolated
restore before production readiness can pass; an older v4 receipt is never
silently promoted to the stronger claim. The restore tool may inspect a legacy
v3 snapshot only as empty/inactive compatibility, but that result cannot pass
the current release-seal or cutover gates.

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
completed, checksum-valid v4 snapshot to private Hetzner Object Storage. That
snapshot includes the full `/var/lib/seiche-nbs` recovery tree and the separate
root-controlled `/var/lib/seiche-palimpsest-china` activation-state tree.
Restricted raw and numeric evidence remains inside the authenticated
ciphertext; offsite status and receipts expose only aggregate hashes, fixed
state paths, and audit-policy labels.

`seiche-market-offsite-backup.service`:

1. holds an exclusive offsite-run lock, then shares
   `/run/lock/seiche-market-backup.lock` with the local producer and restore
   check while it selects the newest committed UTC snapshot; hidden stages,
   extra members, links, malformed manifests, or any checksum failure are
   rejected. The current closed manifest must be `seiche.market-backup.v4`, name the
   exact production NBS root `/var/lib/seiche-nbs`, require
   `seiche.nbs-full-store-audit.v1`, and record
   `nbs_full_store_audit_result=required_at_restore`. It must also name
   `/var/lib/seiche-palimpsest-china`, bind
   `seiche.palimpsest-china-activation-state.v1`, and carry the exact canonical
   audit receipt beside `palimpsest-china.tgz`. Legacy v3 snapshots remain
   restore-compatible only as an explicitly empty, inactive China context;
   v2 snapshots are not accepted;
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
   then verifies every restored source hash, the closed v4/NBS contract, and
   the Palimpsest China archive/audit identity again; and
7. uploads `RECEIPT.json` last, captures and downloads that exact version
   byte-for-byte, and atomically commits root-only status at
   `/var/lib/seiche-offsite-backup/status.json`. Both records bind the source
   backup schema, NBS state root, full-store audit contract, Palimpsest China
   state root, activation-state audit contract, exact tree digest, and active
   versus inactive state. They do not claim `verified_head` and do not publish
   NBS member inventories, Palimpsest bundle contents, or evidence values.

The local record uses `seiche.market-offsite-backup-status.v4`; the immutable
remote completion marker uses `seiche.market-offsite-backup-receipt.v4`.
Version 4 adds the explicit `canary` versus `scheduled` mode, exact active and
pending Palimpsest China activation IDs, and the SHA-256 of the downloaded
immutable remote receipt bytes. A canary success never deduplicates the first
scheduled write, even for the same snapshot; only an exact prior scheduled
success can deduplicate a scheduled retry. Version 3 remains readable for
ordinary historical freshness, but cannot complete a current activation
durability transaction because it lacks those bindings. Version 2 success
records remain readable only for legacy v3 snapshots and cannot authorize a v4
upload because they lack the Palimpsest China state commitments. A version 1
`running` record or failed
record that reached receipt intent remains an unresolved boundary and still
requires operator reconciliation.

Activation durability additionally seals the exact restore-v5 receipt bytes
and digest inside the root-only immutable activation receipt, plus the
scheduled offsite-v4 immutable remote receipt key, digest, and verified clock.
That embedded closed restore proof remains authoritative after the ordinary
21-day local snapshot retention policy removes its historical snapshot; the
mutable current restore receipt must still be a non-regressed successor.
The mutable latest restore/offsite status may advance after a later release,
but readiness accepts it only as a successor: schema v5/v4, the same live
activation ID and canonical tree, no pending candidate, a non-regressing proof
clock, and an equal immutable receipt identity whenever the snapshot is the
same. A fresh inactive or older snapshot never satisfies that contract.

The exact-parent active-marker v1 compatibility path is one-way and
provisional-only. While a legacy eleven-path API environment has no explicit
status variable, every served Palimpsest economic context is still labeled
`provisional`. The locked migration accepts only canonical, fully validated v1
bytes with their matching immutable activation receipt. Marker v2 archives
those exact bytes and SHA-256, requires every historical semantic field to be
equal, and preserves the historical activation ID and release SHA when the same
bundle is resumed by a later signed Seiche release. It creates no owner
acceptance or durability claim. Malformed or unknown v1 fails without rewrite;
a crash before the atomic marker rename retries the same activation, while an
already committed v2 is idempotent. Restore-v5, scheduled offsite-v4, final
live audit, and the outside-tree seal remain mandatory after migration. The
seal's release SHA is the current trusted release that produced those durability
proofs and must equal the embedded restore's deployed SHA and scheduled offsite
source revision; the marker and activation receipt keep their historical
publication release.

The outside-tree durability receipt is deliberately not copied into its own
activation snapshot. A total host/volume restore that loses it therefore
restores the live marker as provisional and fails readiness. Recovery must
resume the same activation ID to produce a new exact local restore, scheduled
immutable offsite proof, final live audit, and local seal; a different
activation or release remains blocked until that replay completes.

A remote attempt without `RECEIPT.json` is incomplete. Recovery must enumerate
receipt versions and use the recorded ciphertext VersionId and SHA-256, never
an unqualified latest-by-key object. Failed attempts may remain immutable until
an operator-managed lifecycle expires them; recovery tooling must ignore them.
A failure after the attempt workspace and status trap are established preserves
the last successful proof inside `last_success` while recording the current
failure. Earlier configuration/snapshot/disk preflight failures are visible in
the failed systemd unit and its OnFailure alert without replacing status.
Any legacy prior `running` status is unresolved. In v4, a pre-receipt
`running`/`failed` status with no receipt key or VersionId can retry while
preserving its exact `last_success`; the job fsyncs a second `running` status
with the receipt key before any immutable receipt upload. From that intent
boundary onward, `running` or `failed` is unresolved. Both the job and installer
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
SEICHE_OFFSITE_BACKUP_PREFIX=seiche/market-backups/v2
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

### Production v1-to-v2 namespace cutover

A host with a successful status-v1 scheduled proof cannot satisfy the new v2
installer from the old prefix, and its deterministic canary objects cannot be
overwritten. Before installing this release, disable the old schedule and move
the unchanged key material and Hetzner destination to a never-used prefix in
canary mode. Keep `KEY_ID=market-key-2026-08-v1` while the passphrase is
unchanged, and keep `DESTINATION_ID=hetzner-primary-v1` while the same
account/project/endpoint/bucket is used. Relabeling either would make recovery
provenance false.

Run the following as root while the offsite-run and shared backup locks are
held. The transformer accepts only the exact nine-line root-owned config,
performs a same-directory fsynced atomic replacement, and preserves every
field except the explicitly named prefix/canary transition:

```bash
atomic_offsite_config_transition() {
  local expected_prefix=$1 new_prefix=$2 expected_canary=$3 new_canary=$4
  /usr/bin/python3 -I -B - \
    /etc/seiche/offsite-backup.env \
    "$expected_prefix" "$new_prefix" "$expected_canary" "$new_canary" <<'PY'
import os
from pathlib import Path
import stat
import sys
import tempfile

path_text, expected_prefix, new_prefix, expected_canary, new_canary = sys.argv[1:]
path = Path(path_text)
expected_fields = {
    "SEICHE_OFFSITE_BACKUP_BUCKET",
    "SEICHE_OFFSITE_BACKUP_PREFIX",
    "SEICHE_OFFSITE_BACKUP_RCLONE_REMOTE",
    "SEICHE_OFFSITE_BACKUP_WRITE_ENABLED",
    "SEICHE_OFFSITE_BACKUP_CANARY",
    "SEICHE_OFFSITE_BACKUP_KEY_ID",
    "SEICHE_OFFSITE_BACKUP_DESTINATION_ID",
    "SEICHE_OFFSITE_BACKUP_RETENTION_MODE",
    "SEICHE_OFFSITE_BACKUP_RETENTION_DAYS",
}
parent_fd = os.open(
    path.parent,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
)
stage_name = ""
try:
    source_fd = os.open(
        path.name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    try:
        metadata = os.fstat(source_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= 8192
        ):
            raise SystemExit("offsite config metadata is unsafe")
        chunks = []
        remaining = 8193
        while remaining:
            chunk = os.read(source_fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks).decode("ascii")
    finally:
        os.close(source_fd)
    if not body.endswith("\n") or "\r" in body:
        raise SystemExit("offsite config encoding is unsafe")
    lines = body.splitlines()
    pairs = [line.split("=", 1) for line in lines]
    if len(lines) != 9 or any(len(pair) != 2 for pair in pairs):
        raise SystemExit("offsite config is not the closed nine-line contract")
    values = dict(pairs)
    if len(values) != 9 or set(values) != expected_fields:
        raise SystemExit("offsite config fields are not exact")
    if (
        values["SEICHE_OFFSITE_BACKUP_PREFIX"] != expected_prefix
        or values["SEICHE_OFFSITE_BACKUP_CANARY"] != expected_canary
        or values["SEICHE_OFFSITE_BACKUP_KEY_ID"] != "market-key-2026-08-v1"
        or values["SEICHE_OFFSITE_BACKUP_DESTINATION_ID"]
        != "hetzner-primary-v1"
    ):
        raise SystemExit("offsite config is not at the expected transition state")
    values["SEICHE_OFFSITE_BACKUP_PREFIX"] = new_prefix
    values["SEICHE_OFFSITE_BACKUP_CANARY"] = new_canary
    replacement = "\n".join(f"{key}={values[key]}" for key, _ in pairs) + "\n"
    stage_fd, stage_path = tempfile.mkstemp(
        prefix=".offsite-backup.env.transition-",
        dir=path.parent,
    )
    stage_name = Path(stage_path).name
    try:
        os.fchown(stage_fd, 0, 0)
        os.fchmod(stage_fd, 0o600)
        pending = memoryview(replacement.encode("ascii"))
        while pending:
            written = os.write(stage_fd, pending)
            if written <= 0:
                raise OSError("short write while staging offsite config")
            pending = pending[written:]
        os.fsync(stage_fd)
    finally:
        os.close(stage_fd)
    os.rename(
        stage_name,
        path.name,
        src_dir_fd=parent_fd,
        dst_dir_fd=parent_fd,
    )
    stage_name = ""
    os.fsync(parent_fd)
finally:
    if stage_name:
        try:
            os.unlink(stage_name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
    os.close(parent_fd)
PY
}

systemctl disable --now seiche-market-offsite-backup.timer
systemctl stop seiche-market-offsite-backup.service
test "$(systemctl is-active seiche-market-offsite-backup.service)" = inactive
atomic_offsite_config_transition \
  seiche/market-backups/v1 seiche/market-backups/v2 0 1
test "$(systemctl is-enabled seiche-market-offsite-backup.timer)" = disabled
test "$(systemctl is-active seiche-market-offsite-backup.timer)" = inactive
```

Do not manually run the old status-v1 service after this transition. Install
the signed candidate, require its v4 local backup and v5 restore receipt, and
then perform the new v2 canary below. The new service independently proves that
`seiche/market-backups/v2/canary/v1` has no versions or delete markers before
its irreversible first write.

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
Change only `SEICHE_OFFSITE_BACKUP_CANARY=0` using the same atomic transformer
defined above (redefine it first if this is a new root shell), rerun the exact
signed-asset `install-market-platform.sh`, and verify timer enablement:

```bash
atomic_offsite_config_transition \
  seiche/market-backups/v2 seiche/market-backups/v2 1 0
/usr/bin/env -i \
  HOME=/root LANG=C.UTF-8 PATH=/usr/bin:/bin \
  SEICHE_PRIVILEGED_ASSET_ROOT="$ASSET_ROOT" \
  SEICHE_RELEASE_TARGET_SHA="$TARGET" \
  SEICHE_NBS_RUNTIME_ROOT=/opt/seiche-nbs-intake \
  /usr/bin/bash -p \
    "$ASSET_ROOT/ops/deploy/install-market-platform.sh"
test "$(systemctl is-enabled seiche-market-offsite-backup.timer)" = enabled
test "$(systemctl is-active seiche-market-offsite-backup.timer)" = active
```

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
