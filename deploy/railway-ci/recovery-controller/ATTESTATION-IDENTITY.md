# Scheduled execution identity

Railway cron assigns a new deployment ID to each scheduled execution. The
original owner-signed installation receipt continues to approve the exact
native image, source, manifest, environment, service, and evidence key. Its
initial deployment ID is historical installation evidence.

The protected OIDC tail runs `attest_live.py` from an independently pinned
`tail_source`. That file must also be included in the protected `tail_inputs`
hash manifest. The native image, original verifier, restore scripts, and
backend input hashes remain unchanged.

`RECOVERY_CONTROLLER_RAILWAY_TOKEN` is a project token scoped to the validation
environment, stored only in the existing protected
`railway-stateful-recovery-export` GitHub environment. It is used exclusively
for fixed read queries. A Railway project token has deployment permissions;
this is a scope restriction, not a claim of read-only provider permissions.
Never provision an account or workspace token for this purpose.

Before and after receipt download, the tail queries the actual signed index's
deployment and replica and checks the exact project, environment, service,
image digest, successful or retained deployment state, and creation clock.
The owner signature and every original immutable object, content, retention,
restore, authority, freshness, and production runtime check still run. A new
image, service, source, manifest, or evidence key requires new owner approval.
The two original subject files are returned without changing their bytes.

`attest.py` remains the sealed original verifier. No cron identity is rewritten
inside an execution index, and no deployment identity is taken on trust from
the index alone. Missing provider credentials or inconsistent provider evidence
release no attestation subjects.
