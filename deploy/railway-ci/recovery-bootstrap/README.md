# Manual recovery after provider logs expire

The daily controller and monitor require a fresh recovery/off-site pair. If a
long interruption exhausts that window and the three-day activation log window,
the protected `export-recovery` workflow can use a durable historical anchor.

Prepare a `seiche.durable-recovery-bootstrap.v1` manifest with the exact current
application source and deployment, the original GitHub run and attempt, and the
immutable off-site receipt key, version, size and SHA-256. The review window must
end within four hours of creation. Store its compressed base64 bytes in the
`railway-stateful-recovery-export` environment secret
`RECOVERY_DURABLE_BOOTSTRAP_ZLIB_BASE64`. Dispatch `export-recovery` with the raw
manifest's digest in `durable_bootstrap_sha256` and the existing
`EXPORT_WITHOUT_AUTHORITY_CHANGE` confirmation.

The helper verifies the original GitHub OIDC attestations for both receipts,
their exact invocation and current application identity, and all original
request/candidate/shadow/activation bindings. It reads six exact immutable metadata
versions using the existing SSE-C and Object Lock helpers; each must retain at
least 29 days of COMPLIANCE protection. No backup data is restored to production.
The historical anchor supplies only the activation hash for a new signed export.
All original writer coordination, export, reverse restore, object sealing,
acknowledgment and attestation steps remain required. It does not satisfy the
daily monitor's freshness check or the native daily execution index.

After the new export is accepted, run the strict monitor, qualify the native
controller installation, and remove the temporary bootstrap secret. Preserve the
manifest and verification proof privately. A mismatched runtime, expired review,
unreadable object, incorrect signature, changed version or short retention fails
before any new export is requested.
