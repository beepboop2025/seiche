# Railway public monitoring

These credential-free Railway jobs preserve the existing public checks:

| Service | CHECK_KIND | UTC cron |
| --- | --- | --- |
| seiche-security-headers | security-headers | `17 4 * * *` |
| seiche-distribution-receipts | distribution-receipts | `41 7 * * *` |
| seiche-discovery-coverage | discovery-coverage | `23 6 * * 1,4` |

Use `ops/railway-automation/Dockerfile`, one replica, `NEVER` restarts and
Wait for CI disabled. Each invocation exits and has a nine-minute deadline.
Only `RAILWAY_PUBLIC_CHECK_PASS` bound to the exact source and deployment
proves a passed check. The JSON discovery scorecard is also written to logs.
Keep the native Railway logs as the run record; GitHub artifact upload is no
longer part of these jobs.

The Dockerfile-specific allowlist includes only the probe code and version
metadata. It does not widen the production Docker build. The probes do not
publish packages, approve a release, change catalog claims or deploy a site.
Native Docker, operating-system and OIDC-dependent release proofs remain
separate from these public monitoring jobs.

## Portable distribution verification

`Dockerfile.distribution` runs the six portable jobs directly from the committed
`distribution-contracts.yml`: offline metadata and frontend contracts, the R
client, native dataset metadata, reproducible Seiche packages, OpenBB Python
3.10–3.14 compatibility, and reproducible OpenBB provider builds. Each job gets
an isolated Python environment; original commands and pinned dependencies are
used without copying them into a second hand-maintained script. Unknown actions,
expressions, conditionals and workflow output formats fail closed.

The Docker image contract job and PostgreSQL18 Docker tools proof still require
a Docker executor. `RAILWAY_DISTRIBUTION_PORTABLE_PASS` explicitly describes
portable coverage and does not claim those jobs or publisher attestations.
Only exact committed public sources are fetched; no publishing credentials or
public endpoint belong on this service. Maximum runtime is one hour.

The portable distribution gate runs during the image build. Runtime verifies
the recorded build source and emits deployment-bound proof; it does not repeat
the full suite. Native Railway PR environments run these jobs for the owner;
non-owner PRs retain their GitHub fallback. The hardened Docker contract job
continues independently until its permanent native executor is proven.

Native CI fetches current main and requires it to be an ancestor of the tested
head. A stale pull request fails admission and must merge/rebase current main
before another run. The build logs record both exact head and base revisions;
the native status always describes the tested head itself.
