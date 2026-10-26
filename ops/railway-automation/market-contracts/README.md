# Market platform contracts on Railway

Credential-free native Railway build gate for all four Python/PostgreSQL17/root
permission-boundary steps in `market-platform-ci.yml`. Commands are read directly
from that source workflow so a changed test list cannot silently lose coverage.
The image includes the pinned Caddy adapter and hash-locked social-card renderer.

The first three suites run as the unprivileged runner. The root journal boundary
step starts as root and drops to runtime UID 10001 inside the test, preserving
the real permission checks under BuildKit's `no-new-privileges` policy without
requiring a later privilege escalation. Its workflow command remains unchanged.

The PostgreSQL18 Docker build/restore harness remains in the separately confined
Railway rootless executor. Both proofs are required for complete original job
coverage. Keep the original GitHub PR check until the Docker reporting App can
report both exact-head success and a deliberately failing owner PR.

Connect only in the credential-free public PR base environment with no variables,
volumes or production links. Build failures and runtime isolation failures both prevent acceptance. The runtime
report checks the exact source before and after the mandatory Linux UID/Landlock
tests, requires zero failures and zero skips, then emits the final pass marker.
The kernel boundary runs in the deployed container because BuildKit exposes a
different sandbox; no isolation check is skipped or converted into a build pass. The test database uses
only local throwaway credentials and is stopped at exit.
