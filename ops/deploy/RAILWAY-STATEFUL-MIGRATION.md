# Railway stateful migration (phase 4 shadow)

Phase 4 restores one exact, committed `seiche.market-backup.v4` snapshot into
Railway without moving production authority. Hetzner remains the sole writer,
public origin, rollback target, and source of release and recovery evidence.
The Railway service has no public domain and starts no collectors, workers,
publisher, Telegram bot, or execution surface. Its only child is a private,
read-only-use API for health and compatibility probes.

This is a migration rehearsal, not a cutover. A successful run proves that the
five durable state domains can be reconstructed together:

1. PostgreSQL market metadata and snapshots;
2. `/var/lib/seiche` market state and exports;
3. `/var/lib/seiche-nbs` restricted and signed public evidence; and
4. `/var/lib/seiche-palimpsest-china` immutable bundles, receipts, and
   active/pending markers; and
5. the API compatibility/SQLite tree.

## Topology and authority

```text
Hetzner exact-SHA backup-v4 (authoritative, writers running)
             |
             | operator stages nine immutable files
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
Do not add a Railway SSH private key to GitHub: Railway user/workspace SSH keys
are broader than this project, and the protected workflow neither opens SSH nor
reads volume files. The project token is its only Railway credential.

## Select and stage a snapshot

On Hetzner, first require the live SHA and its recovery receipt to be complete.
Under the shared backup lock, select one committed snapshot whose
`deployed-sha.txt` is that exact 40-character SHA. Verify:

```bash
cd /var/backups/seiche-market/REVIEWED_UTC_SNAPSHOT
sha256sum --check --strict SHA256SUMS
test "$(find . -mindepth 1 -maxdepth 1 -type f | wc -l)" -eq 9
```

Copy the nine files through the existing reviewed operator channel to a
private local directory. Do not stage from an unqualified off-site `latest`
key: use the exact ciphertext VersionId and receipt if the off-site recovery
path is the source.

Address the exact Railway project, environment, stateful service, and volume
on every file operation; do not rely on an ambient CLI link. Upload into a new
snapshot-specific inbox; the CLI refuses replacement, and a partially
populated inbox cannot validate:

```bash
for member in seiche.dump var-lib-seiche.tgz palimpsest-china.tgz \
  palimpsest-china-state.json api-data.tgz table-counts.txt deployed-sha.txt \
  manifest.env SHA256SUMS; do
  railway volume \
    --project REVIEWED_PROJECT_ID \
    --environment REVIEWED_ENVIRONMENT_ID \
    --service REVIEWED_STATEFUL_SERVICE_ID \
    files --volume REVIEWED_STATEFUL_VOLUME_ID upload \
    "REVIEWED_PRIVATE_SNAPSHOT_DIR/$member" \
    "/inbox/REVIEWED_UTC_SNAPSHOT/$member" \
    --json
done
```

This staging command is a manual operator action using the operator's reviewed
Railway access. It is deliberately outside GitHub Actions. Keep the private
source directory until the run is accepted so a failed or incomplete inbox can
be reconciled without granting CI broader filesystem access.

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
   PostgreSQL reference, and staging prerequisites;
3. uploads the pinned stateful image, waits for that exact deployment, and
   proves its `/healthz` manifest and one exact `RUNNING` instance;
4. has the runtime validate every staged snapshot member and tar path before
   extraction; CI never downloads or lists volume files;
5. runs SQLite `PRAGMA quick_check`, the strict full NBS store audit, a fresh
   generation-specific PostgreSQL restore, and four table-count floors;
6. writes a canonical, immutable, group-readable shadow receipt;
7. starts only the API as uid/gid 10001, with control tokens removed and the
   stateful pre-activation read-only guard enabled;
8. emits one bounded, opaque, single-line log envelope only after the runtime
   has revalidated that canonical receipt;
9. retrieves exact-deployment JSON logs with the project token, reconstructs
   the exact receipt bytes, and runs the independent receipt verifier;
10. restarts that same deployment, requires exactly one `reused` envelope whose
    Railway log timestamp is after the recorded restart boundary, and requires
    its receipt bytes and digest to be identical to the created result; and
11. re-proves the exact deployment is `SUCCESS`, has one `RUNNING` instance,
    retains the private evidence for 90 days, and OIDC-attests those exact
    canonical receipt bytes.

The log envelope is base64 transport, not encryption. Its closed receipt
schema is designed to contain no database URL, token, key, or other secret;
deployment logs and retained artifacts must still be treated as private
migration evidence. Any future receipt field that could contain a secret must
be rejected before this transport is extended. The extractor is bounded and
fails closed on malformed or truncated encoding, duplicate lifecycle markers,
an unexpected lifecycle, stale request/deployment/replica identities, a
non-canonical receipt, or a restart marker at or before the local not-before
boundary.

Completion requires all workflow steps green and an attestation for the
reconstructed canonical receipt. A Railway `SUCCESS`, a green health check, a
log marker, or an uploaded artifact alone is insufficient.

The shadow receipt contract is
`seiche.railway-stateful-shadow-receipt.v4`; v3 receipts are deliberately not
accepted. It contains one closed `palimpsest_china_state` identity with exactly
`audit_schema`, `tree_sha256`, `active_activation_id`, and
`pending_candidate_activation_id`. Those values come from the canonical
backup-v4 audit, not filenames or mutable configuration. The pending identity
must be null. On creation, restart, and reuse, Railway re-audits the restored
tree as the configured uid/gid 10001 reader and requires the semantic tree and
active activation ID to remain byte-for-byte equal to the receipt. The
independent filesystem-tree digest remains a separate whole-tree integrity
bound; neither digest substitutes for the other.

The v4 filesystem proof also embeds a closed
`seiche.agent-room.restore-audit.v1` result. For initialized Agent Room state,
the restore loads the restored operator key without creating or replacing it,
opens the existing SQLite store, verifies every participant key binding, and
audits each room's signed genesis and client/server-signed event hash chain.
The receipt records the verified server-key ID, bounded counts, state digest,
and the fixed non-executable/no-authority policy. It also verifies the immutable,
key-bound initialization seal stored under `_attest`; a seal without its
database or an initialized database without its seal is state loss, not an empty
bootstrap. A truly never-initialized room is recorded explicitly as
`absent_uninitialized` only when both database and seal are absent, with zero
counts and a null state digest. If the restored attestation directory already
contains an operator key, the receipt records that key ID as the only identity
under which production may later bootstrap the room. If no key exists, the
receipt carries a closed `unprovisioned` runtime gate: shadow, candidate, and
the resulting production release may not mint a room identity. Partial state,
an unbound or changed key, or signed-chain drift fails both the initial restore
and restart/reuse validation.

The shadow API does not start the warm loop, rebuild or reseal a board, create
an attestation key, database, or initialization seal, or open the legacy
SQLite fallback. It hydrates only an already validated active PostgreSQL
handoff produced by this exact release; absence or another release remains
unready. The Agent Room is either absent or opened through its read-only audit
path, and every non-read HTTP method is rejected before routing. These guards
preserve the point-in-time tree hashes: any pre-activation byte or semantic
mutation still fails restart/reuse validation.

## Repeat and reconcile

The request ID and filesystem/database generation names are content-addressed.
Redeploying the same accepted request re-hashes the filesystem, reruns SQLite
and NBS verification, and requires unchanged PostgreSQL counts before serving.
The Phase 4 workflow exercises this reuse path with `railway restart`; Railway
may preserve the deployment and replica IDs, so restart proof comes from the
same deployment's later Railway log timestamp plus the unique `reused`
lifecycle envelope, not from assuming an ID must change. Any mutation fails
closed.

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
- successful exact-deployment restart/reuse validation with one later
  `reused` marker and byte-identical canonical receipt; and
- a v4 shadow receipt carrying the closed Palimpsest China identity, restored
  Agent Room audit, and no pending activation transaction; and
- an operator-reviewed rollback and authority-fencing rehearsal.

Phase 5 uses a bounded maintenance freeze, one final snapshot, a content-bound
authority fence, an authenticated read-only edge state, and a separately
protected activation grant. Its implementation and exact operator sequence are
in `RAILWAY-STATEFUL-CUTOVER.md`.

Until all three Phase 4 runs are accepted, Phase 6 recovery controls are
green, and the final Phase 5 activation receipt is acknowledged on Hetzner,
Hetzner remains production even when every shadow check is green.
