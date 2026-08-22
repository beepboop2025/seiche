# Seiche storage on a Hetzner Volume

Seiche's durable market state and local recovery snapshots must live on the
pinned Hetzner Volume. A directory with the right name is not sufficient: if a
mount or bind mount is missing, Linux otherwise writes into the server's root
filesystem without warning.

The guarded production paths are:

```text
/var/lib/seiche
/var/backups/seiche-market
```

`seiche-storage-preflight.service` runs before the API, source and market
workers, backfill, validation, backup, restore check, readiness check, and
release poller. It fails closed unless all of the following are true:

- the configured mount path is the Volume filesystem root itself, not a
  fallback directory or bind-mounted subdirectory;
- its live block-device major/minor, UUID, and filesystem type match the pinned
  configuration;
- both guarded paths are exact bind mountpoints on that same device, and their
  filesystem roots match two pinned, distinct, non-nested Volume directories;
- available blocks and inodes meet the configured floors; and
- a create, write, file fsync, unlink, and directory fsync succeeds through
  both guarded paths.

Every consumer also declares `RequiresMountsFor` for both paths. The preflight
is intentionally a non-resident oneshot: each new consumer start transaction
runs it again, and the five-minute readiness job supplies a continuous probe.

## Provisioning contract

Provision and format the Volume outside the application deploy. Use the stable
Hetzner `/dev/disk/by-id/scsi-0HC_Volume_*` identity in `/etc/fstab`; do not pin
an assignment such as `/dev/sdb`, which may change after reboot. Mount the
Volume at a dedicated path such as `/mnt/seiche-volume`, create separate state
and backup directories on it, and bind-mount those directories onto the two
guarded production paths. The mount and both bind mounts must be present before
running the market-platform installer.

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
/usr/bin/python3 /etc/seiche/libexec/seiche-storage-preflight.py \
  --config /etc/seiche/storage-volume.env
```

Then inspect the resolved identities without changing state:

```sh
for path in /mnt/seiche-volume /var/lib/seiche /var/backups/seiche-market; do
  findmnt --target "$path" -o TARGET,SOURCE,FSTYPE,UUID,MAJ:MIN,FSROOT
done
systemctl status seiche-storage-preflight.service --no-pager
```

`SEICHE_STORAGE_EXPECTED_STATE_FSROOT` and
`SEICHE_STORAGE_EXPECTED_BACKUP_FSROOT` must match the two `FSROOT` values
reported for the bind mounts. This prevents a valid Volume mounted with its
state and backup subdirectories accidentally swapped, aliased, or nested.

## Scope of this phase

This guard covers the filesystem evidence tree at `/var/lib/seiche` and the
local recovery snapshots at `/var/backups/seiche-market`. It does **not** move
the native PostgreSQL cluster (`PGDATA`) or the compatibility/API tree at
`/home/seiche/app/backend/data`; backups copy both of those into the guarded
snapshot path, but their live copies remain on the host root disk. Moving them
requires separate database and application-data migration plans, capacity
checks, restore drills, and rollback receipts. Do not report this phase as
"all Seiche data transferred."

## Cutover sequence

The cutover is an operator action, not part of an ordinary release:

1. Disable and stop all five persistent timers:
   `seiche-release-poll.timer`, `seiche-data-readiness.timer`,
   `seiche-market-validation.timer`, `seiche-market-backup.timer`, and
   `seiche-market-restore-check.timer`.
2. Let any active deploy finish or stop `seiche-release-poll.service` cleanly.
   In one root operator shell, acquire and hold both
   `/run/seiche-control/release.lock` and `/run/seiche-deploy/deploy.lock` with
   `flock`; do not proceed if either lock is busy. The second lock also blocks
   forced-SSH deployment while the timer is disabled.
3. Stop the API, source/market workers, backfill, validation, backup, restore,
   and readiness services. Confirm all are inactive. Inspect `systemctl` plus
   `fuser -vm /var/lib/seiche /var/backups/seiche-market` and stop any reviewed
   ad-hoc writer before the final copy.
4. Mount the new Volume and stage copies of state and backups while retaining
   numeric ownership, modes, ACLs, xattrs, hard links, sparse files, and mtimes.
5. Compare file inventories and checksums, then do a final no-change copy pass.
6. Install the two bind mounts and the root-controlled configuration above.
7. Run the preflight, the market-platform installer, one real backup, and one
   isolated restore check. Confirm the exact deployed SHA and strict API/data
   readiness before re-enabling the five timers and releasing the two locks.
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

The Volume removes the two guarded paths from the laptop and gives the
production host durable attached block storage. It is not an off-node backup.
The live state and `/var/backups/seiche-market` remain on one Volume and share a
failure domain; independent disaster recovery still needs a separately
reviewed off-node destination and restore drill.
