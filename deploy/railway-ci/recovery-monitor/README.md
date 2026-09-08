# Railway recovery monitor

This trusted, manually deployed controller preserves the strict `monitor` job
from `railway-stateful-recovery.yml`: native state-volume and PostgreSQL backup
schedules/freshness/canaries, live PITR coverage, storage headroom, production
readiness, matching origin/public identities, and a recent exact recovery/offsite
receipt pair. It never requests an export, restores data, changes schedules,
publishes an attestation or sends a Telegram message.

`prepare.py` builds a small context from one reviewed source revision. The image
contains the original probe/proof/cleanup scripts and the exact five required
source helpers. At runtime, public main is fetched without credentials and its
helpers and monitor script/environment definitions must match those reviewed
bytes. Only the three verified Python package files are materialized. Fetched
application code cannot replace the controller, modify the read-only command
allowlist or acquire the service's credentials. A monitor change fails closed
until a new reviewed image is deployed.

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
