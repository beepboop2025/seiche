# Native migration publication admission

This is a staged successor, not production publication authority. The existing
Railway and nine-document Hetzner SSE-C/ObjectLock contracts remain separate.
The new package is `seiche.native-migration-catalog-source-health.v1` and the
contained evidence is `seiche.native-migration-publication-evidence.v1`.

The package must contain actual `grant`, `acceptance`, `transfer`,
`final_restore`, `release`, `boot`, `restart`, `backup`, `backup_readback`,
`monitor`, `source_equivalence`, and `source_acceptance` documents. The adapter
executes the real runtime grant, final-transfer/restore, and acceptance
validators. It retains complete final state, parent, image, volume, all four
trees, database count floors, Agent Room identity and Palimpsest bindings.

The accepted release and restart prove the actual private socket and named
Caddy ACL, exact application/PostgreSQL containers, systemd owners, unchanged
cluster identity, PostgreSQL-before-application cold restart, one supervisor,
one API and two workers, and fresh worker heartbeats. The monitor consumes the
root operator's `seiche.native-monitor.v1` format directly. Both health
observations must identify the same current Hetzner production runtime and
snapshot producer, contain no source faults or legacy Railway identity, and
retain complete provenance and the existing 15-minute freshness limit.

The backup format is the actual native online component backup. Both database
and filesystem artifacts, plus the exact complete manifest, require independent
encrypted Drive readback with object IDs, exact sizes and SHA-256. A queued
upload, a controls-only backup, or a transport hash is insufficient. These
online backups are not a globally fenced snapshot and have no WORM/COMPLIANCE
retention guarantee. A parsed PostgreSQL archive is not a disposable restore.
Those limitations remain explicit; the migration's separately verified final
fenced restore is still mandatory. No backup receipt creates writer authority.

For a publication source different from immutable runtime source
`795ace8fa691674de022e1f83e5545769a388c6c`, the source bridge requires an owner
signature in namespace `seiche-native-publication-source-v1`, principal
`seiche-native-publication-release`, using the existing pinned owner public key.
Its four-hour window permits initial admission only. An immutable, root-owned
acceptance written inside that window permits later reuse of the same exact
source/tree pair, runtime image and grant; signatures and complete inventories
are rechecked every time. Changing any bound source, image, grant, or file
requires a new reviewed approval. A fabricated or backdated acceptance is not
authority; only the protected operator may persist it after validation.

The bridge records every backend entry, including mode and blob identity.
Only the five named test/fixture differences with the exact pinned before/after
blob pairs are reviewable; there is no directory-wide test exception. Runtime
and all other backend bytes must match. The complete publication tree and
controller inventory are also signed. The bridge grants no execution, writer,
or raw-data publication authority. The existing signed release tag, corpus
receipt, catalog consistency, PyPI artifact and live semantic gates still run.
The historical descendant exception is available only after complete native
authority/recovery and cryptographic exact-tree validation.

`ops/release/prepare_native_publication.py` provides root-only `check`,
`accept-source`, and `prepare-package` commands. It accepts canonical private
evidence, writes exclusive private files, never signs or publishes, and never
starts or changes a runtime. `accept-source` rechecks the signed admission
window immediately before durable output. `prepare-package` requires explicit
publication intent and request IDs. FULL still revalidates its package before
public writes and uses durable request claims; a static file cannot refresh
health observations. No timer or publisher is activated by these changes.

FULL takes one new observation after the sealed frontend build and before its
first public write. The operator must mount a protected observer directory
read-only and atomically refresh the package there; a bind mount of one old
file inode cannot supply an updated observation. Only the monitor and paired
health observations may change. Intent, request, signed source approval,
accepted runtime, transfer, restore, restart and backup documents must remain
exactly equal. FULL runs the complete gate again and pins that replacement for
all later claims. Missing, changed-authority or stale packages fail closed;
neither a long build nor a scheduled refresh extends the freshness deadline.

Production gates still open during preparation: root review of the exact
combined source/tree and five-path manifest; actual accepted runtime and cold
restart receipts; encrypted Drive artifact/manifest readback; fresh immutable
monitor proof; owner-signed bridge and durable acceptance; exact-source native
FULL prepare/build and public artifact verification. The old publisher must
not be described as compatible with the new runtime until these gates pass.
