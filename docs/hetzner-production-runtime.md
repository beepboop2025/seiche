# Explicit Hetzner production runtime

This is a separate authority contract from Railway and from the read-only
Hetzner standby. The standby release and its receipts remain valid evidence of
that earlier rehearsal. They cannot authorize this runtime.

The reverse-transfer controller must first close old database admissions,
terminate all old application writers, durably retire the old authority pointer,
capture the final state, and verify its isolated restore. Only then may the
owner issue a fresh signed Hetzner grant. A controller receipt must never
describe a live earlier export as the final fenced state.

The application runs as UID/GID 10001. Start it using
`python -B -m seiche.hetzner_runtime`. The supervisor starts one API, one market
worker and one source worker. The workers initialize their authority before
entering the existing collector loops. The API listens on the private Unix
socket named in the root runtime observation. There is no automatic child
restart: any exited child or retired authority stops the entire group.
Authority is checked on a two-second supervisor poll, on API requests, before
repository connections, between collection cycles, and before snapshot and
source-summary publication. Revocation uses immediate process-group
termination, including descendants whose original group leader already exited.
A later transfer must also close PostgreSQL
admissions and drain/terminate existing transactions; killing processes alone
is not a final database fence.

## Root authority files

Set `SEICHE_HETZNER_AUTHORITY_DIR` to a directory mounted read-only for the
application. Every ancestor and file must be root-owned and not writable by
group/others. Files must be single-link regular files, never symlinks. JSON uses
sorted keys, compact separators, UTF-8, no non-finite numbers, and one final
newline. Receipt SHA-256 values hash the complete file, including that newline.
Modes such as root:10001 directories 0750 and files 0440 allow application reads.

| File | Required contents |
|---|---|
| `grant.json` | `seiche.hetzner-runtime-approval.v1`, `payload`, SSH `signature` |
| `transfer.json` | Accepted `seiche.hetzner-final-transfer.v1`; exact digest in grant |
| `restore.json` | Accepted `seiche.hetzner-final-restore.v1`; exact digest in grant |
| `acceptance.json` | Durable root acceptance of this exact grant within its signed time window |
| `active.json` | Current root pointer for this runtime and grant; `state: active` |
| `runtime.json` | Root-observed host, volume, image, source, data and HTTP socket binding |

The grant payload has exactly these fields:

```text
schema = seiche.hetzner-runtime-grant.v1
provider = hetzner
authority = production
runtime_id = 64 lowercase hexadecimal characters
issued_at, expires_at = UTC timestamps, lifetime at most one hour
code = {commit: Git SHA, tree: Git tree SHA}
transfer_sha256, restore_sha256 = exact root evidence file hashes
host = {identity: SHA256(stripped host machine-id), volume_uuid, image_digest}
data = {generation_root, database_name, postgres_socket, postgres_port,
        postgres_role: seiche_runtime}
agent_room = {expected_key_id: audited key SHA256 or unprovisioned}
edge_token_sha256 = SHA256(private ASCII edge token)
```

The SSH signature covers the canonical payload **without** a final newline.
Use namespace `seiche-hetzner-runtime-v1` and principal
`seiche-hetzner-runtime-release`. The owner public key is pinned in
`backend/seiche/hetzner_runtime.py`; no private signing key belongs in the
application environment or container.

`acceptance.json` has exactly `schema`, `runtime_id`, `grant_sha256`,
`transfer_sha256`, `restore_sha256`, `code`, and `accepted_at`, with schema
`seiche.hetzner-runtime-acceptance.v1`. Its timestamp must fall inside the signed
grant window. An expired grant can restart only when this original durable
acceptance and the current active pointer still match. The runtime never
creates or backdates acceptance.

`active.json` has exactly `schema: seiche.hetzner-runtime-active.v1`,
`state: active`, `runtime_id`, and `grant_sha256`. The root controller retires or
replaces this pointer before another writer can be granted authority.

`runtime.json` has exactly `schema: seiche.hetzner-runtime-observation.v1`,
`runtime_id`, `grant_sha256`, `code`, `host`, `data`, and `http_socket`.
The code/host/data fields must equal the grant. The controller must observe the
actual container image and mounted volume; an environment string does not
independently prove either. Mount the real host machine-id read-only at the path
provided through `SEICHE_HETZNER_HOST_ID_PATH`; the runtime hashes its contents.
The signed checkout must include its Git metadata and remain clean.

## Final restore and database

The final restore retains `source_backup.source`, `filesystem.generation_root`,
`filesystem.agent_room_audit`, `filesystem.validated_parent_handoff`, and
`postgres.parent`, `counts`, `count_floor`, `database_name`, `postgres_socket`,
`postgres_port`. Add `postgres.runtime_database_role: seiche_runtime`.
The final restore schema is `seiche.hetzner-final-restore.v1`, status `PASS`,
with `authority_changed`, `writer_grant_issued`, and `public_origin_changed`
all false. Its `handover_id`, exact code, image digest, host identity, volume,
complete-state comparison, capture hash and capture snapshot ID must match
the transfer and signed grant. `source_backup.transfer_index_sha256` binds
the retained transfer inventory. `filesystem.status` must be `PASS`, with
SHA-256 entries for exactly `api`, `market`, `nbs`, and `palimpsest-china`.
The parent handoff projection has `commit`, `handoff_id`, `envelope_sha256`,
and `payload_sha256`.

The root restore controller adds `filesystem.palimpsest_environment` by calling
`stateful_migration.palimpsest_runtime_environment()` on the restored
`palimpsest-china` tree. An inactive tree yields `{}`. An active tree yields all
eleven file paths from one audited bundle beneath that generation. The runtime
rejects incomplete, mixed, inherited or foreign paths and retains the existing
`provisional` publication label. Agent Room similarly uses the signed expected
key; an `unprovisioned` binding cannot mint a new Room identity.

Provision a distinct `seiche_runtime` role that owns only the isolated
application database and its application tables. It must have no superuser,
CREATEDB, CREATEROLE, replication, BYPASSRLS, role memberships, or CREATE
privilege on any other database. Existing runtime schema convergence needs
ownership of the application tables. Preserve the separate `seiche_reader`
role and its restrictions. The runtime connects through the exact private Unix
socket, with no password, host-address or service overrides in the DSN.

`SEICHE_DATABASE_URL` must parse to exactly `dbname`, `host`, `port`, and `user`
matching the grant. Set `SEICHE_ENV=production`, exact `SEICHE_RELEASE_SHA`,
both heartbeat-required settings to `1`, and every path returned by
`hetzner_runtime.runtime_paths(grant, restore)`. The four `SEICHE_HETZNER_*` variables are
`AUTHORITY_DIR`, `GRANT_SHA256`, `EDGE_TOKEN`, and `HOST_ID_PATH`.
Any Railway, standby or `PG*` environment setting is rejected. Prebuilt handoff
environment overrides are rejected.

The edge token is passed in `X-Seiche-Hetzner-Token`; the root proxy must inject
it privately. Keep ordinary subscriber Authorization semantics separate.
Responses identify provider `hetzner`, authority `production`, actual source
SHA and runtime ID. Public routing remains a separate controller operation.

## Readiness and retained evidence

At startup, only the exact final restored parent or a valid handoff from this
same source can seed the cache. Its original date, version and producer remain
unchanged. Every startup treats that cache as retained evidence, including
restarts of the same source. It does not satisfy `require_rebuilt=true`.

Strict readiness needs a completed new-source build with its sealed handoff and
both worker heartbeats observed after this API incarnation started. Startup
retains subsecond precision, so a heartbeat from before a fast restart within
the same second cannot pass. A stale
parent cache, an old worker heartbeat, a wrong producer or incomplete sealing
returns 503. Source-access denial behavior and collector rights checks remain
the existing product implementation.

The existing release-handoff activation boundary remains separate from writer
authority: the release controller must validate and promote the exact staged
handoff before accepting durable release publication. This runtime does not
silently promote a pending snapshot or change public origins.

## Activation and retained restart

The root controller prepares and validates the complete authority directory
before enabling it, writes the durable acceptance only within the signed grant
window, and installs the exact active pointer last. It must observe the real
container image, source checkout, machine ID, volume UUID and private socket
mounts before writing `runtime.json`. Bind the source and authority directory
read-only, give UID/GID 10001 only its dedicated generation and HTTP socket
directory, and provision the exact private PostgreSQL peer mapping.

Run `python -B -m seiche.hetzner_runtime` as UID/GID 10001 with the configured
environment. Probe `/api/health?require_rebuilt=true` through the private HTTP
Unix socket with `X-Seiche-Hetzner-Token`. A successful strict probe establishes
the newly built producer and fresh worker evidence. Read the exact candidate
from `/api/internal/v1/release-health` through the same private socket. Preserve
the public proxy's existing denial of this internal capability endpoint.

The release controller starts a separate UID/GID 10001 process with the same
runtime environment, calls `hetzner_runtime.initialize()`, then
`assemble.activate_pending_snapshot(producer_sha, activation_token)`. Require
`True`, reload the repository's active handoff, and verify the exact producer
and token before durable release acceptance. Only the root deployment owner
can then change the public route. An ordinary restart repeats all startup
checks and accepts an expired grant solely through the unchanged durable
acceptance and active pointer.
