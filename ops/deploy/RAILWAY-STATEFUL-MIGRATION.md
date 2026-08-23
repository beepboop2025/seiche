# Railway stateful migration (phase 4 shadow)

Phase 4 restores one exact, committed `seiche.market-backup.v3` snapshot into
Railway without moving production authority. Hetzner remains the sole writer,
public origin, rollback target, and source of release and recovery evidence.
The Railway service has no public domain and starts no collectors, workers,
publisher, Telegram bot, or execution surface. Its only child is a private,
read-only-use API for health and compatibility probes.

This is a migration rehearsal, not a cutover. A successful run proves that the
four durable state domains can be reconstructed together:

1. PostgreSQL market metadata and snapshots;
2. `/var/lib/seiche` market state and exports;
3. `/var/lib/seiche-nbs` restricted and signed public evidence; and
4. the API compatibility/SQLite tree.

## Topology and authority

```text
Hetzner exact-SHA backup-v3 (authoritative, writers running)
             |
             | operator stages seven immutable files
             v
Railway stateful-core service -- one volume at /var/lib/seiche-platform
             |
             +-- generation-specific filesystem trees
             +-- immutable shadow receipt
             |
             +-- private Railway PostgreSQL
                         |
                         +-- generation-specific restored database

No domain, no production secret, no Hetzner connection, no worker, no cutover
```

Use one replica for the stateful service. Do not enable horizontal replicas,
overlapping deployments, a GitHub-connected autodeploy source, or a public
domain. The deployment is uploaded explicitly by the protected workflow. The
volume is mounted only at runtime, so snapshot bytes must be staged with the
Railway volume file interface before dispatch.

## One-time Railway bootstrap

Create these resources inside the same dedicated Railway project/environment
as the stateless gates:

- service `seiche-stateful-core`, with one replica and no domain;
- volume `seiche-stateful-data`, mounted exactly at
  `/var/lib/seiche-platform` on that service; and
- Railway PostgreSQL, exposed to the service through the private
  `DATABASE_URL` reference.

Create the protected GitHub environment `railway-stateful-migration`. Require
review for deployment and add these secrets without printing their values:

- `RAILWAY_TOKEN`
- `RAILWAY_PROJECT_ID`
- `RAILWAY_ENVIRONMENT_ID`
- `RAILWAY_STATEFUL_SERVICE_ID`
- `RAILWAY_STATEFUL_VOLUME_ID`
- `RAILWAY_STATEFUL_VOLUME_NAME`
- `RAILWAY_STATEFUL_REGION`

The token must be project-scoped. The stateful service must not receive a
Hetzner database URL, deploy key, object-storage credential, NBS signing key,
API credential, Telegram token, GitHub token, or DNS credential. Railway's own
PostgreSQL reference is the sole database secret available to the root restore
supervisor; the supervisor removes the control `DATABASE_URL` before starting
the unprivileged API and supplies only the generation-specific database URL.

## Select and stage a snapshot

On Hetzner, first require the live SHA and its recovery receipt to be complete.
Under the shared backup lock, select one committed snapshot whose
`deployed-sha.txt` is that exact 40-character SHA. Verify:

```bash
cd /var/backups/seiche-market/REVIEWED_UTC_SNAPSHOT
sha256sum --check --strict SHA256SUMS
test "$(find . -mindepth 1 -maxdepth 1 -type f | wc -l)" -eq 7
```

Copy the seven files through the existing reviewed operator channel to a
private local directory. Do not stage from an unqualified off-site `latest`
key: use the exact ciphertext VersionId and receipt if the off-site recovery
path is the source.

Link the Railway CLI to the exact project, environment, and stateful service.
Upload into a new snapshot-specific inbox; the CLI refuses replacement, and a
partially populated inbox cannot validate:

```bash
railway link --project REVIEWED_PROJECT_ID \
  --environment REVIEWED_ENVIRONMENT_ID \
  --service REVIEWED_STATEFUL_SERVICE_ID

for member in seiche.dump var-lib-seiche.tgz api-data.tgz \
  table-counts.txt deployed-sha.txt manifest.env SHA256SUMS; do
  railway volume files upload \
    --volume REVIEWED_STATEFUL_VOLUME_ID \
    "REVIEWED_PRIVATE_SNAPSHOT_DIR/$member" \
    "/inbox/REVIEWED_UTC_SNAPSHOT/$member"
done
```

Compute the workflow inputs from those exact local bytes. The content-set
digest is SHA-256 over, in `SHA256SUMS` order, each ASCII filename, NUL, member
digest, NUL, decimal byte length, and newline. This is the same closed
calculation enforced by `seiche.stateful_migration` and the immutable off-site
receipt. Also record the SHA-256 of the exact live release receipt and matching
recovery receipt; do not substitute IDs or reformatted JSON.

## Dispatch and proof

Dispatch `.github/workflows/railway-stateful-shadow.yml` on the exact `main`
SHA with:

- snapshot UTC ID;
- snapshot `SHA256SUMS` digest;
- closed content-set digest;
- exact live release-receipt digest;
- exact recovery-receipt digest; and
- confirmation `HETZNER_REMAINS_SOLE_WRITER`.

The workflow then:

1. creates canonical source archive, Git bundle, and shadow request bytes;
2. proves the exact isolated service/volume, absent public domain, private
   PostgreSQL reference, and staged metadata;
3. uploads the pinned stateful image and waits for that exact deployment;
4. validates every snapshot member and tar path before extraction;
5. runs SQLite `PRAGMA quick_check`, the strict full NBS store audit, a fresh
   generation-specific PostgreSQL restore, and four table-count floors;
6. writes a canonical, immutable, group-readable shadow receipt;
7. starts only the API as uid/gid 10001, with control tokens removed;
8. retrieves and independently validates the receipt, probes `/healthz`
   through the exact active instance, retains private evidence for 90 days,
   and OIDC-attests those exact receipt bytes.

Completion requires all workflow steps green and an attestation for the
downloaded canonical receipt. A Railway `SUCCESS`, a green health check, or an
uploaded artifact alone is insufficient.

## Repeat and reconcile

The request ID and filesystem/database generation names are content-addressed.
Redeploying the same accepted request re-hashes the filesystem, reruns SQLite
and NBS verification, and requires unchanged PostgreSQL counts before serving.
Any mutation fails closed.

Never delete or overwrite an inbox, generation, database, or receipt merely to
make a retry pass. A failed run that created an unreceipted generation is an
explicit reconciliation boundary: preserve deployment logs, inspect the exact
volume and database generation, record the incident, and use a new reviewed
snapshot/request only after deciding the prior state can be retired.

## Exit criteria for phase 5

Run at least three shadow restores from three exact releases. Record queue,
build, upload, restore, health, and attestation durations plus snapshot size and
Railway CPU/memory/storage peaks. Phase 5 cutover remains disabled until all
three runs have:

- exact source/release/recovery binding;
- closed backup and strict NBS proof;
- database counts at or above their floors;
- no Railway public domain and no Railway writer;
- successful restart/reuse validation of the receipted generation; and
- an operator-reviewed rollback and authority-fencing rehearsal.

Phase 5 uses a bounded maintenance freeze, one final snapshot, a content-bound
authority fence, an authenticated read-only edge state, and a separately
protected activation grant. Its implementation and exact operator sequence are
in `RAILWAY-STATEFUL-CUTOVER.md`.

Until all three Phase 4 runs are accepted, Phase 6 recovery controls are
green, and the final Phase 5 activation receipt is acknowledged on Hetzner,
Hetzner remains production even when every shadow check is green.
