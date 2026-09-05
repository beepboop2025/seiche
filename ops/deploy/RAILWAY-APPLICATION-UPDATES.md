# Updating the application after Railway activation

The migration activation identifies its original source and recovery baseline.
Do not relabel that receipt, restore its frozen backup over current state, or
pass an old deployment ID to new code. Application updates use a separate
`seiche.railway-application-request.v1` request and activation receipt.

The operation changes code on the same project, environment, service and volume.
It does not migrate databases, add execution authority, or resume Hetzner writers.
There is a bounded read-only maintenance interval while the replacement mounts
the volume, checks current state and waits for its signed writer grant.

## Release preparation

1. Finish the current activation's portable export, isolated restore, immutable
   off-site readback, OIDC verification and strict post-export monitor. Keep the
   original root acknowledgement and writers frozen. Do not update during export.
2. Review and test the new commit, preserve signatures and ancestry, advance the
   exact tested commit to main, and hold content-generating schedules. Give changed
   package contents a new version; an existing PyPI version remains immutable.
3. Independently verify the current recovery and off-site attestations. Prepare a
   private parent directory containing canonical `activation.json`, `candidate.json`,
   `shadow.json`, `recovery-request.json`, `recovery.json`, and `offsite.json`.
   These are copies of accepted evidence, not edited templates.
4. Read the actual target's project/environment/service/volume/name/mount/region
   into a canonical seven-field JSON file. Prepare a new empty image context:

   ```sh
   PYTHONPATH=backend python ops/railway/build_application_context.py \
     --repo "$PWD" --commit "$APPLICATION_SHA" \
     --parent-dir "$PARENT_EVIDENCE" --railway-target "$TARGET_JSON" \
     --output "$NEW_CONTEXT"
   ```

   The helper verifies signed source, ancestry, exact Git archive/bundle, parent
   receipt bindings and fresh off-site proof. It does not deploy. The context
   expires after one hour. Its Dockerfile differs from the source only by one
   explicit `COPY parent/ /migration/parent/` line. Validate SSH signatures using
   the actual image's `/usr/bin/ssh-keygen`, including a tampered-message rejection.

## Stopped-source and activation approvals

Submit the context once to the existing service. Record the new deployment UUID
and replica from Railway; never infer either from a version or filename. Observe
all predecessor instances as `EXITED` or `STOPPED`, and verify the frozen Hetzner
writer units remain stopped. Only then sign the `source_stopped` payload defined
by `stateful_application.validate_source_fence` and publish it atomically at:

`/var/lib/seiche-platform/application-approvals/<request-id>.source-stopped.json`

Approvals contain exactly `schema`, `purpose`, `payload`, and `signature`. Sign
canonical JSON of the first three fields, including its final newline, with the
existing release key through `ssh-keygen -Y sign`. The dedicated namespace is
`seiche-railway-application-v1`; the trusted public key is pinned in the contract.
The private key stays in the operator's SSH agent/key store. A Git signature or
a signature for another approval purpose cannot authorize this transition.

The new root supervisor audits the existing generation and database in place.
Critical table counts and Agent Room state must extend the latest parent recovery;
immutable NBS and Palimpsest trees retain their original identities. It starts a
read-only candidate and writes `<request-id>.application-candidate.json` in the
existing `cutover-receipts` directory. Prove the exact new SHA and deployment on
both origin and public API. The existing edge origin is unchanged.

Sign the `activate` payload defined by `validate_grant`, including the exact
request, candidate, predecessor activation, destination, edge-token digest and
public-probe digest. Publish it atomically to
`application-approvals/<request-id>.activate.json`. The contract requires the
confirmation `STOPPED_PARENT_CURRENT_DATA_NEW_APPLICATION`.

The supervisor stops the candidate API, repeats the current-data audit, retires
the migration grant (or supersedes the previous application pointer), durably
accepts the new grant, starts writers, seals the new activation receipt and starts
the production API. Old receipts remain unchanged. Root-owned directories and a
lifetime lock serialize updates; only the active application may restart writers.
Completed predecessor requests are validated against their original activation
and atomically moved to `recovery-request-history/<parent-activation-sha>/` before
new authority is accepted. Their bytes and receipt files remain unchanged. This
keeps the current recovery loop from interpreting historical work as a new request.
Online SQLite backup uses bounded pages and yields between them so API metering
can write during the copy; an individual copy has a fifteen-minute deadline.

## Interrupted operations and final acceptance

- Before a new grant is accepted, reconcile the same pending request. Do not
  restore old state or resume the retired predecessor. An expired unaccepted
  approval requires a reviewed recovery decision; expiry grants no authority.
- After `authority/application-grants/<request-id>.json` exists, only that same
  request/deployment may resume. A crash may leave an accepted grant without an
  activation receipt. Restarting that same image resumes the accepted transition.
- A completed successor requires its exact active pointer, accepted grant,
  activation receipt and actual provider deployment. Replaying an earlier image
  fails before writers start. A rollback is another signed successor transition,
  not restarting an old deployment with stale authority.
- Require fresh fault-free public and private health, exact new application and
  deployment identity, and feature-specific REST/MCP results. Then run Phase6
  export, isolated restore, off-site readback, OIDC and the strict monitor again.
  Recovery receipts identify the new application and retain the original migration
  candidate/shadow as their immutable data baseline.
- Re-enable content-generating schedules only after the exact-main release hold
  is released. Keep the legacy Hetzner deployment and writer units disabled.

Application approvals are SSH signatures, not GitHub OIDC attestations. The
subsequent recovery workflow independently attests its actual export and off-site
receipts. Retain both types of evidence with their correct provenance.
