# Hetzner successor recovery and publication

`ops/release/hetzner_release.py` validates the actual accepted Hetzner runtime.
It never translates a Railway deployment UUID or grants runtime authority.
The existing Railway publication branch retains its original checks.

The protected `runtime_evidence` object has exactly nine documents: `grant`,
`acceptance`, `release`, `boot`, `restart`, `capture`, `offsite`, `restore`, and
`monitor`. `validate_evidence(evidence, source=commit, now=utc_clock)` checks the
owner's SSH-signed runtime grant, exact commit/tree/host/volume/image, root
acceptance inside the signed window, enabled app and PostgreSQL systemd owners,
and a completed restart of both exact containers. PostgreSQL must precede the
app, retain the same cluster identifier, remain socket-only, and stay within
1 CPU/1.5 GiB; the app/helper budget is 1 CPU/2 GiB.

Successor capture uses `seiche.hetzner-successor-capture.v1`. A journaled stop
drains the API and both workers before a complete PostgreSQL dump and logical
catalog/all-table/sequence/large-object manifest, all four filesystem trees,
SQLite logical contents, AgentRoom audit, NBS audit and Palimpsest activation
audit. The ordinary backup-v4 nine-member format remains unchanged. The
capture receipt records same-authority resume and cannot claim success for
an incomplete or still-running helper. Capture age is limited to 26 hours.

`seiche.hetzner-successor-offsite.v1` records the actual SSE-C AES256 object,
customer-key fingerprint, immutable version, complete version-pinned download
hash, and at least 90 days of COMPLIANCE retention from object creation. The
package hash identifies bytes returned after SSE-C decryption; it is not a
claim about inaccessible provider ciphertext. Existing credentials remain
in memory in the already accepted Object Lock client.

`seiche.hetzner-successor-restore.v1` accepts only an independent isolated
executor, the exact downloaded object version, the actual runtime/PostgreSQL
image IDs, and matching complete manifests and audits. No production path is
mounted. A 10 GiB free-space floor and 100,000 free inodes remain reserved.
The production app is stopped during capture; an independent restore runs
separately so its PostgreSQL and helper do not exceed the production budget.

`seiche.hetzner-successor-monitor.v1` binds the chain to a current exact runtime,
single supervisor, current worker heartbeats, retained source fence and
offsite retention. Its complete private-origin and public health observations
must agree, identify Hetzner production, use `Cache-Control: no-store`, and
carry the same exact release and snapshot producer. Both monitor and rebuilt
snapshot must be no older than 15 minutes. Source faults and their original
provenance/clocks remain visible.

Catalog publication requires `seiche.hetzner-catalog-source-health.v1`, which
adds `runtime_evidence` to the existing private package. The context uses
`provider=hetzner` and `runtime_id`; its target contains `host_identity`,
`volume_uuid`, `image_digest`, `grant_sha256`, and `recovery_sha256`. Controller
and runtime source commits must be identical. No source-equivalence exception
is supported in this branch.

Healthy publication uses null `body_b64` and `signature_b64`; it still consumes
one durable publication request bound to the complete recovery and health
pair. A known source HTTP failure requires the owner's separately signed
`seiche.hetzner-source-degradation.v1` admission in SSH namespace
`seiche-hetzner-source-degradation-v1`. It retains the original bounded fault
allowlist and never permits raw-data publication, execution, source changes,
arbitrary faults, or silent removal of provenance. Scheduled FULL publication
must remain strictly healthy and must not import a temporary fault admission.
The FULL controller accepts `HETZNER_HEALTH_PACKAGE_FILE` only as a private
owner file containing that healthy package. Its scheduling operator must issue
a fresh package for each invocation; a static environment path does not renew
the monitor or snapshot clocks. FULL revalidates the original package after
the build and immediately before both public writes, then persists the exact
claim. An expired package or a remaining source fault holds the run; the
controller never refreshes observations, creates an exception, or claims
natural recurrence from a prepared file.

Tags, package artifacts, registry/container/archive channels, original
publication result and marker, prior-tree rollback, public bytes and later
scheduled recurrence remain distinct acceptance gates. A recovery or a
successful HTTP response does not establish those outcomes or continuous uptime.
