# Seiche storage on a Hetzner Volume

Seiche's durable market state, signed NBS evidence, and local recovery snapshots
must live on the pinned Hetzner Volume. A directory with the right name is not
sufficient: if a mount or bind mount is missing, Linux otherwise writes into
the server's root filesystem without warning.

The guarded production paths are:

```text
/var/lib/seiche
/var/lib/seiche-nbs
/var/backups/seiche-market
```

`seiche-storage-preflight.service` runs before the API, source and market
workers, backfill, validation, backup, restore check, readiness check, and
release poller. It fails closed unless all of the following are true:

- the configured mount path is the Volume filesystem root itself, not a
  fallback directory or bind-mounted subdirectory;
- its live block-device major/minor, UUID, and filesystem type match the pinned
  configuration;
- all three guarded paths are exact bind mountpoints on that same device, and
  their filesystem roots match three pinned, pairwise distinct and non-nested
  Volume directories;
- the NBS bind root is owned by `root:seiche` with exact mode `0750`;
- available blocks and inodes meet the configured floors; and
- a create, write, file fsync, unlink, and directory fsync succeeds through
  all three guarded paths.

Every consumer requires the guarded mounts directly or through its mandatory
dependency on this all-three-path preflight. The preflight is intentionally a
non-resident oneshot: each new consumer start transaction runs it again, and the
five-minute readiness job supplies a continuous probe.

## Provisioning contract

Provision and format the Volume outside the application deploy. Use the stable
Hetzner `/dev/disk/by-id/scsi-0HC_Volume_*` identity in `/etc/fstab`; do not pin
an assignment such as `/dev/sdb`, which may change after reboot. Mount the
Volume at a dedicated path such as `/mnt/seiche-volume`. The signed production
namespace is `/seiche/runtime/var-lib-seiche` for state,
`/seiche/evidence/seiche-nbs` for signed evidence, and
`/seiche/backups/seiche-market` for local recovery snapshots. Bind-mount those
three directories onto the guarded production paths. The mount and all three
bind mounts must be present before running the market-platform installer. Set
the Volume's NBS directory to `root:seiche` mode `0750`; the preflight verifies
that inode metadata on every run and never repairs it.

Copy `storage-volume.env.example` to:

```text
/etc/seiche/storage-volume.env
```

Replace every placeholder with values observed from the mounted Volume, then
set the file to root ownership, one link, and mode `0640` (or `0600`). The
minimum-block setting is expressed in the filesystem's native block units; on
an ext4 filesystem with 4 KiB blocks, the example floor of `2621440` is 10 GiB.

Before enabling a service, run the same production probe directly:

```sh
/usr/bin/python3 -I -B /etc/seiche/libexec/seiche-storage-preflight.py \
  --config /etc/seiche/storage-volume.env \
  --state-path /var/lib/seiche \
  --nbs-path /var/lib/seiche-nbs \
  --backup-path /var/backups/seiche-market
```

Then inspect the resolved identities without changing state:

```sh
for path in /mnt/seiche-volume /var/lib/seiche /var/lib/seiche-nbs /var/backups/seiche-market; do
  findmnt --target "$path" -o TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN,FSROOT
done
systemctl status seiche-storage-preflight.service --no-pager
```

`SEICHE_STORAGE_EXPECTED_STATE_FSROOT`,
`SEICHE_STORAGE_EXPECTED_NBS_FSROOT`, and
`SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT` must match the three `FSROOT` values
reported for the bind mounts. This prevents a valid Volume mounted with its
state, signed-evidence, and backup subdirectories accidentally swapped,
aliased, or nested.

## Migrating the storage contract from v1 to v2

Schema v2 intentionally rejects the former two-bind v1 configuration. The NBS
path is evidence-bearing state; treating a missing v2 field as an optional
root-disk directory would defeat the fail-closed boundary.

Perform the migration as a stopped-service operator cutover. Hold both deploy
locks plus the offsite-selection and shared backup locks, stop the persistent
timers and data consumers described below, create the exact
`/seiche/evidence/seiche-nbs` directory on the mounted Volume, and bind it onto
`/var/lib/seiche-nbs` through `/etc/fstab`. Before mounting, the target must be
an empty, real `root:root` directory sealed against fallback writes (mode `000`
is suitable); after mounting, the visible source inode must be `root:seiche`
mode `0750`. Verify that bind with `findmnt`
before atomically replacing `/etc/seiche/storage-volume.env` with a root-owned
`0600` or `0640` v2 file containing `SEICHE_STORAGE_NBS_PATH` and
`SEICHE_STORAGE_EXPECTED_NBS_FSROOT`. Run the v2 preflight directly before
starting any installer or consumer. Never create `/var/lib/seiche-nbs` as a
fallback data directory to make the migration proceed.

## Scope of this phase

This guard covers the filesystem evidence tree at `/var/lib/seiche`, the signed
NBS intake and public-revision store at `/var/lib/seiche-nbs`, and local recovery
snapshots at `/var/backups/seiche-market`. It does **not** move the native
PostgreSQL cluster (`PGDATA`) or the compatibility/API tree at
`/home/seiche/app/backend/data`; backups copy those host-root datasets into the
guarded snapshot path, but their live copies remain on the host root disk.
Moving them requires separate database and application-data migration plans,
capacity checks, restore drills, and rollback receipts. Do not report this
phase as "all Seiche data transferred."

## Cutover sequence

The cutover is an operator action, not part of an ordinary release:

1. Capture the enabled/active state, then disable and stop all six persistent
   timers:
   `seiche-release-poll.timer`, `seiche-data-readiness.timer`,
   `seiche-market-validation.timer`, `seiche-market-backup.timer`, and
   `seiche-market-restore-check.timer`, plus
   `seiche-market-offsite-backup.timer`. Stop any still-active backup, restore,
   or offsite service and wait for it to exit cleanly.
2. Let any active deploy finish or stop `seiche-release-poll.service` cleanly.
   In one root operator shell, acquire and hold
   `/run/seiche-control/release.lock`, `/run/seiche-deploy/deploy.lock`, and
   `/run/lock/seiche-market-offsite-backup.lock`, then
   `/run/lock/seiche-market-backup.lock` with `flock`; do not proceed if any
   lock is busy. Preserve that lock order. The deploy lock also blocks
   forced-SSH deployment while the timer is disabled; the offsite lock freezes
   snapshot selection, and the backup lock excludes local backup, restore, and
   guarded NBS intake.
3. Stop the API, source/market workers, backfill, validation, backup, restore,
   and readiness services. Confirm all are inactive. Inspect `systemctl` plus
   `fuser -vm /var/lib/seiche /var/lib/seiche-nbs /var/backups/seiche-market`
   and stop any reviewed ad-hoc writer before the final copy.
4. Mount the new Volume and stage copies of state, NBS evidence, and backups
   while retaining numeric ownership, modes, ACLs, xattrs, hard links, sparse
   files, and mtimes.
5. Compare file inventories and checksums, then do a final no-change copy pass.
6. Install the three bind mounts and the root-controlled v2 configuration above.
7. Run the preflight, the market-platform installer, one real backup, and one
   isolated restore check. Confirm the exact deployed SHA and strict API/data
   readiness before restoring the six timers to their captured pre-cutover
   states and releasing all four locks. Never enable a timer that was disabled
   before the cutover merely to make this checklist green.
8. Keep the old source data read-only until the new mount has survived a reboot
   and the post-reboot backup/restore receipt is green. Only then is removal a
   separately authorized cleanup step.

Do not delete or reformat the old data as part of the copy step. A failed copy
or mount can be rolled back; premature deletion cannot.

## Failure behavior

If the Volume is absent, mounted from the wrong block device, full, out of
inodes, read-only, or unable to fsync, `seiche-storage-preflight.service` fails,
triggers the existing Undertow failure alert, and prevents the dependent job
from starting. Diagnose the mount; never create an empty fallback directory to
make the service green.

Dependency rejection can leave consumers failed even after the mount is fixed;
recovery is deliberately operator-controlled. Repair and verify the mount,
run the preflight directly, then `systemctl reset-failed` for the API, backfill,
workers, validation, backup, restore check, and readiness units. Start the API,
backfill/market worker, and source worker in that order; run one backup and
isolated restore check; run readiness; only then restart their timers and the
release poller. Preserve the exact-SHA deploy receipt throughout this sequence.

The same floor gates backup startup, while normal retention runs only after a
new snapshot commits. If free blocks or inodes are already below the floor,
stop the timers and treat pruning as a manual recovery action: first verify a
newer committed snapshot, its checksum inventory, a green restore receipt, and
any required independent copy; then remove only an explicitly approved,
top-level timestamped snapshot. Never lower the floor or delete hidden staging
paths merely to turn the preflight green.

The Volume gives the three guarded paths durable attached block storage on the
production host. It is not an off-node backup. Live market state, NBS evidence,
and `/var/backups/seiche-market` remain on one Volume and share a failure
domain; independent disaster recovery still needs a separately reviewed
off-node destination and restore drill.
