# Scoped source health admission

The default release and recovery path requires an empty source fault list.
`ops/release/source_degradation.py` adds an owner-signed, short-lived admission
for a complete, explicitly observed set of registered source HTTP failures.
It reports `evidence_status: degraded`; it never asserts runtime readiness or
changes a health response, source observation time, value, or permission.

The outer operation must still pass its runtime, storage, provider identity,
worker, backup, restore, authority and source-rights checks. This admission
cannot grant source access, execution authority or raw-data publication rights.
It performs no network calls, upstream retries, signing or durable claim I/O.

## Signed contract

The canonical UTF-8/ASCII JSON body uses sorted keys, compact separators and a
single trailing newline. Duplicate keys, non-finite values, extra schema fields
and documents larger than 16 KiB are rejected. The signature uses SSH namespace
`seiche-source-degradation-v1` and the existing pinned owner public key.

The body contains exactly:

- `schema: seiche.source-degradation.v1`;
- `context`, with the exact purpose, 64-character lower-case hexadecimal
  `intent_id`, controller and runtime source SHAs, application target UUIDs,
  deployment UUID, expected semantic version and authority, and operation;
- UTC `issued_at` and `expires_at`, with a maximum lifetime of four hours;
- `faults` and `faults_sha256`, a sorted complete fault set and SHA-256 of its
  canonical bytes;
- `limits`, whose `source_permission`, `execution_authority` and
  `raw_data_publication` values must all be false.

Only the fixed collector identities in `KNOWN_SOURCES` are eligible. At most
eight distinct faults are admitted. Each fault must contain exactly `source`,
`status: FAILED`, `category: HTTP_ERROR` and the fixed sanitized detail
`official source returned an HTTP error`. This category does not prove an AI
ban or identify the HTTP status. Per-series identifiers and other error
classes require a separately reviewed contract extension.

The purposes are `recovery`, `native_recovery`, `application_candidate`,
`application_activation`, `application_handoff`, and `catalog_metadata`.
Their operation-specific fields are validated as closed shapes in
`validate_context`. A caller must construct expected context from its trusted
operation, provider identity and owner-reviewed version. It must not copy an
unverified context wholesale from the admission or derive the expected version
from the health response it is checking.

## Assessment and replay

`document` only parses; it does not authenticate. `verify_admission` verifies
signature, schema, lifetime and expected context. `assess` additionally requires
current origin and public health observations, each with `body` and `identity`
(`source_sha`, `deployment_id`, `authority`). Both must match the expected
identity, version, fault set, generated snapshot time and provenance. Snapshot
age must be between zero and 900 seconds; provenance must be nonempty. Provider
project/environment/service and active-instance checks remain the outer
operation's responsibility.

`assess_internal` is restricted to `application_handoff`: the internal rebuilt
candidate is assessed immediately before the existing handoff write while the
public route still serves its durable parent snapshot. It retains the same
signature, identity, version, freshness, provenance and exact-fault checks.
The caller must authenticate the internal transport, candidate activation
token, signed handoff payload and parent. Other purposes cannot substitute this
single observation for the public/origin pair.

`assess(None, None, ...)` is the strict healthy path. If the source recovers,
callers can use that path without passing an old admission. A presented
admission must still validate and match the current complete fault set.

`claim_document` produces deterministic claim bytes bound to the admission,
full context, expiry and actual execution. `verify_claim` compares an existing
claim to a fresh assessment. These functions do not persist claims or make an
assessment an operation authorization.

Before an action, the outer operation must atomically and durably claim the
admission digest and intent ID for its actual execution. An existing different
claim fails closed; exact-operation resumption is permitted only while a fresh
assessment still validates. Manual recovery binds the actual GitHub run and
attempt without predicting their IDs before dispatch. Native recovery binds
the installed controller deployment, manifest and UTC execution day and retains
the existing lock/index. Local application operations bind their existing
request ID and parent activation digest. Read-only monitors may assess without
consuming an admission.

No claim or policy should live indefinitely. Expiry, a changed source fault,
a changed deployment, or a different operation requires a new observed and
owner-signed admission. Signed source and installation manifests must include
the validator bytes; an unverified mutable helper must never enter a privileged
operation.

## Explicit catalog publication

Catalog admission additionally binds `operation.health_pair_sha256` to the
canonical complete `{origin, public}` pair. The existing authorized operator
observes the private origin, signs the admission and durably claims its original
publication request before dispatch. No origin credential enters static CI.

The `publish-static` manual input `source_health_package` (or catalog verifier
CLI argument `--source-health-package`) carries one JSON object with exactly:

- `schema: seiche.catalog-source-health.v1`;
- `intent_id` and `publication_request_id`;
- base64 `body_b64` and `signature_b64`;
- `health_pair`, containing the unchanged `origin` and `public` observations.

The verifier reconstructs its context from the exact controller source, signed
application tag, pinned production target, current public deployment and
authority, local version, exact catalog file SHA-256 and the pair digest. It
verifies the owner signature and requires a new public body/identity to equal
the signed pair. Both snapshots must still be current within 900 seconds.
Package hashes, versioned PyPI artifacts, corpus checks, signed tags and
discovery checks remain mandatory.

The output includes a deterministic `claim_document` and explicitly states
`claim_persistence: external_operator`; validation does not claim that the
operator's durable record was written. The newly qualified native static
publisher accepts an explicit `--source-health-package` private-file argument
and atomically persists that claim on its existing evidence volume before
either public write. Its assembly pins the helper separately. It repeats the
signed-health assessment after preparation and immediately before mirror and
Pages writes; GitHub's optional manual path rechecks at the same boundaries.
The package may only resume the same original publication request, catalog,
source and deployment before expiry. The manual input cannot be combined with
a frontend-only receipt. Automatic and full-publisher invocations remain strict;
no environment switch grants an exception, and no Actions workflow is enabled
by this code.
