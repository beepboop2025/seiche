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
