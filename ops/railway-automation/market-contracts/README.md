# Market platform contracts on Railway

Credential-free native Railway build gate for all four Python/PostgreSQL17/root
permission-boundary steps in `market-platform-ci.yml`. Commands are read directly
from that source workflow so a changed test list cannot silently lose coverage.
The image includes the pinned Caddy adapter and hash-locked social-card renderer.

The PostgreSQL18 Docker build/restore harness remains in the separately confined
Railway rootless executor. Both proofs are required for complete original job
coverage. Keep the original GitHub PR check until the Docker reporting App can
report both exact-head success and a deliberately failing owner PR.

Connect only in the credential-free public PR base environment with no variables,
volumes or production links. Build failure is the native App status; the short
runtime receipt identifies the source/image that passed. The test database uses
only local throwaway credentials and is stopped at exit.
