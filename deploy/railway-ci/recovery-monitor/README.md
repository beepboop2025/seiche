# Railway recovery monitor

This trusted, manually deployed controller preserves the strict `monitor` job
from `railway-stateful-recovery.yml`: native state-volume and PostgreSQL backup
schedules/freshness/canaries, live PITR coverage, storage headroom, production
readiness, matching origin/public identities, and a recent exact recovery/offsite
receipt pair. It never requests an export, restores data, changes schedules,
publishes an attestation or sends a Telegram message.

`prepare.py` builds a small context from one reviewed application revision. The image
contains the original probe/proof/cleanup scripts and the exact five required
source helpers. At runtime, public main is fetched without credentials and its
helpers and monitor script/environment definitions must match those reviewed
bytes. Only the three verified Python package files are materialized. Fetched
application code cannot replace the controller, modify the read-only command
allowlist or acquire the service's credentials. A monitor change fails closed
until a new reviewed image is deployed.

The application revision (`policy.source`) is the exact accepted runtime to
monitor. It supplies `RECOVERY_SOURCE_SHA` and the proof's `source`; it is never
inferred from a live response. The signed controller revision is recorded
separately in `policy.controller_source`. Fetched main supplies `GITHUB_SHA` and
the proof's `workflow_source`, after the unchanged helper and workflow checks.
This permits a reviewed frontend-only main advance while the application still
runs the earlier accepted revision. Both edges must match that pinned application
revision, even when they agree with each other on some other revision. Missing
or malformed application identities fail before admission or probing.

When the application or governed monitor helpers change, prepare a new context
with that explicitly accepted application revision and qualify its actual image.
Do not reuse a context merely because its controller code has not changed.

Supply a non-secret target JSON containing the six `TARGET_NAMES` in `monitor.py`:

```sh
python prepare.py --repository /path/to/seiche --revision REVIEWED_SHA \
  --signer-public-key /trusted/owner-signing-key.pub \
  --target /private/monitor-target.json --output /private/monitor-build
```

Create one service in the validation environment only, without a repository
trigger, PR environment instance, public endpoint or shared variables. Set only
these three credential variables on that service:

- `MONITOR_RAILWAY_TOKEN`: existing protected monitor API credential.
- `MONITOR_RAILWAY_EDGE_TOKEN`: existing origin health-header credential.
- `MONITOR_RAILWAY_RECOVERY_PROBE_SSH_KEY`: existing registered PostgreSQL probe key.

The project/environment/service/volume/origin target is fixed in the reviewed
image, separate from Railway's injected controller deployment identifiers.
The original SSH wrapper accepts only the exact PostgreSQL instance and two
checksum-pinned read-only probe bodies; the agent and private temporary key are
removed on exit. Origin credentials are passed in a private header file.

Mount a dedicated `/evidence` volume; retain accepted proof and bounded diagnostic
files for 90 days. Use one replica, `NEVER` restarts, a 30-minute process deadline,
and an explicit Tini start command. Count only `RAILWAY_RECOVERY_MONITOR_PASS` as
success. The proof identifies Railway's actual deployment and source; it does
not claim a GitHub run ID or an OIDC attestation.

The standalone `17 */6 * * *` monitor schedule runs on Railway after strict
live proof on September 8, 2026, using deployment
`ba098059-3b2e-4838-bd14-36bf04e95348` with bootstrap disabled. The governed
recovery export, isolated reverse restore, external Object Lock and both
GitHub attestations passed in run `34241275921` before this cutover.
The existing `31 2 * * *` GitHub monitor prerequisite remains while
the protected export job consumes its GitHub artifact and identity. Preserve
manual monitor/export/resume operations and the complete attestation chain.
Rollback is to remove the new Railway schedule and restore the previous GitHub
monitor scheduling condition; the original protected secrets remain available.

Tests execute the exact workflow validator against complete, stale-backup,
unhealthy-PITR, split-edge and low-headroom evidence. They also verify rejected
source drift, scrubbed Git environment, restricted probe environment and missing
recovery-pair rejection. Run the tests during image build and inspect the live
strict proof before retiring any schedule.

`test_monitor.py` retains its original ten tests and original build-log output.
`test_monitor_roles.py` runs separately during image build, including full monitor
environment/proof tests against the unchanged workflow validator. Its detailed
output is retained in `/controller/monitor-role-tests.log`; the matching JSON
receipt records its exact test inventory, sources, prepared manifest and log
hash. Only `RAILWAY_RECOVERY_MONITOR_ROLE_TESTS_PASS` reports this additional
suite. It is not a live monitor result and does not replace the original ten-test
image proof. Qualification must verify both results on the same actual image,
retain their full logs, and separately accept a fresh strict runtime proof.

The same role-test build step emits one `RAILWAY_RECOVERY_MONITOR_ROLE_LOG`
record containing the complete detailed log as base64. Its explicit byte length,
SHA-256, receipt SHA-256, application/controller identities and manifest bind it
to the preceding role-test receipt. The complete encoded build record is limited
to 8 KiB; an oversized log fails the build. A read-only verifier can recover the
original bytes from the bounded build stream after the replica stops, without
SSH or another execution. The encoded record does not introduce a second plain
unittest summary into the original ten-test reader's stream. Local transport
tests are separate from the unchanged ten original and thirteen role image tests.
