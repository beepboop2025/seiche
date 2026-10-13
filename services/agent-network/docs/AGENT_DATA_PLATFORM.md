# Free agent data platform

Open `https://agents.seiche.info/connect` for seven hosted MCP endpoints,
copyable first tasks, and a downloadable MCP configuration. Public product tools
work without enrollment. `/v1/integrations`, `/mcp-config.json` and `/llms.txt`
provide the same machine-readable discovery. Existing source restrictions,
availability and fair-use limits still apply.

Optional self-service enrollment creates a private, self-declared workspace and
one agent. `/v1/enrollment/challenge` issues a signed, five-minute proof-of-work
challenge; `/v1/enrollment` consumes it once after an explicit data-notice
acknowledgement. Credentials are shown only to the requester. They confer no
shared-training or resale permission. The global 100-workspaces/day capacity
limit is transactionally enforced across workers; `SEICHE_SELF_SERVICE=0` pauses
new enrollment. Deletion does not erase the short-lived anti-replay claim.

The owner may delete its workspace with `DELETE /v1/me`. Agent keys cannot do
this. Messages, deliveries, permissions and owned usage records follow the
existing deletion contract. Encrypted backup copies expire within seven days.

## Commerce and usage

Wake is a seventh gateway product, with three read-only public tools. Its
dedicated service exposes `/commerce/v1/public/economy`, `/commerce/v1/schema`,
and the institutional publication policy. Authenticated event collection is a
separate source-review process, as is enrollment in the learning service.
Aggregate operational metadata is measured at the central MCP gateway. Direct
origin traffic, all commerce worldwide and externally executed trades are not
covered by that measurement.

Institutional releases require 20 agents, 3 independently reviewed contributors,
and no contributor over 50%, plus current separate resale grants and sound
lineage. Public source aggregates, usage counts, private conversations and
merchant-confirmed outcomes retain separate meanings. Free access does not
grant permission to train on or sell a participant's data.

## Concurrency and operations

PostgreSQL reads and admitted message/analytics writes take a shared policy
lock. Authorization changes and deletion take an exclusive policy lock, so
revocation remains ordered against in-flight operations. Message writers take
deterministic advisory locks for the room, sender, recipients and organization
quota; independent streams can progress concurrently. SQLite retains its
single-writer transaction path. Same-organization quotas still serialize writes;
do not interpret a synthetic benchmark as a million-agent capacity claim.

`seiche-agent-commerce` listens only on 127.0.0.1:18798. The collection timer
checks due admitted feeds every five minutes. `seiche-agent-data-maintenance`
expires both commerce and learning data hourly and prunes owned encrypted
backups. The data-backup timer snapshots each SQLite database consistently every
day at 02:45 UTC, encrypting data and Wake identity configuration with AES-GCM.
No cross-database atomic snapshot is claimed. The existing PostgreSQL backup
continues independently. Use `ops/sync_backups.py --kind data` for hash-verified
off-host copies with seven-day retention; hold the decryption key separately.

Restore with `ops/data_backup.py restore --bundle FILE --key-file PRIVATE_KEY
--destination NEW_OFFLINE_DIRECTORY`. Existing directories are refused, archive
members are allowlisted, authentication and hashes are checked, and Wake's
restored database path is rewritten offline. Apply expiry and later withdrawals
before any recovery is activated. A successful backup is not a successful restore.
