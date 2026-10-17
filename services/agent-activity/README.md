# Agent Activity Evidence

Public observations first; explicitly authorized private integrations alongside them.
This service extends the evidence product with agent discovery, economic activity
collection, attribution review and independently reconciled contributor records.

The achievable claim is coverage of specified sources, accounts and time windows.
The worldwide population of economically active AI agents is unknown. Registrations,
wallets, services, transactions and people are different units; none is silently
promoted into a count of unique autonomous agents.

Public URL: <https://agents.seiche.info/activity/>. API documentation is at `/docs`,
the complete admission contract at `/v1/contract`, and read-only MCP at `/mcp`.

## What is implemented

| Component | Behavior and evidence boundary |
| --- | --- |
| ERC-8004 discovery | Rolling 500-entry daily sample of the anonymous 8004scan list. Owner addresses are not assumed to be trading accounts. |
| x402 discovery | Rolling 500-service hourly sample. Provider-reported calls and payers are not AI-buyer counts. |
| Virtuals ACP | Contiguous finalized Base log windows, reviewed ABI, block-hash checks, immutable events, checkpoint advance in the same database transaction. Rate limits remain visible. |
| Olas marketplace | Public Gnosis RPC, three pinned marketplace event topics and contiguous finalized windows. Counts represent event logs, not batch members, purchases or independent service-quality verification. No partnership or credential is needed for this source connection. |
| Hyperliquid | Fills only for reviewed, separately publication-authorized account links. No guessed wallets or fabricated initial panel. |
| Private ingestion | Separate contributor credentials, typed authority, immutable receipts, UTC permission window, encrypted payloads, 90-day retention and withdrawal. |
| Python / JavaScript clients | Explicit submission only. Python adds an encrypted, destination-bound, durable outbox with retry and receipt validation. |
| Alpaca adapter | Contributor-side OAuth GET activity reads, bounded pagination and paper/live separation. Account history stays source-reported until a separate independent reconciliation mechanism is provided. |
| Commerce adapter | Merchant/payment receipt normalization after caller verifies its provider webhook. Digital services and physical goods remain separate domains; authorization, settlement, fulfillment and refund are separate events. |
| Reconciliation | Exact Hyperliquid fill match after reviewed agent/version/account/time attribution. Matching quantity, side, instrument, time and supplied price/fee; no inferred profitability. |
| Institutional candidates | Closed weeks, evidence available by cutoff, current separate aggregate-resale grants, deduplicated evidence, 20 reviewed accounts, 3 independent contributors, at most 50% concentration. Suppression and current revalidation are mandatory. |
| Operations | Isolated service, collector timer, bounded encrypted backups, offline restore drill, explicit per-source status and additive route deployment. |

Commerce ingestion does not yet independently verify merchant settlement or physical
delivery. Existing Wake commerce models remain authoritative for their separate
settlement/fulfillment windows. This service does not overwrite Wake or the existing
learning/network databases. Full portfolio returns require complete positions,
cash flows, costs and valuation history; the service does not publish a performance
leaderboard from incomplete fills.

The public protocol endpoint supports `source=virtuals_acp|olas_marketplace` and
returns both sources by default. Each row identifies its chain and source.
The broader route before platform partnerships is documented in
[`PUBLIC_SOURCES.md`](PUBLIC_SOURCES.md), including open-source collection tools,
direct operator contributions and the distinction between software and data rights.

## Local development

```sh
uv sync --frozen --extra dev
uv run pytest -q
uv run agent-activity --state local-state init --operator-output local-state/operator.json --backup-key-output local-state/backup.key
uv run agent-activity --state local-state collect
uv run agent-activity --state local-state serve --operator-file local-state/operator.json
```

Bind only to loopback. State directories must be owned and mode 0700; credential
and encryption-key files must be owned and mode 0600. Local state is ignored by Git.
Never submit production credentials, prompts, raw tool arguments, payment details
or private trading strategies as event labels or evidence hashes' preimages.

## Contributor integration

Only enroll after reviewing the source's authority, purpose, independent affiliation,
permission period and deletion obligations. A pilot request is not enrollment.
Existing network/learning tokens are not accepted. Permission applies prospectively;
events predating enrollment are rejected. A broker or merchant integration must
prove that attribution belongs to the claimed agent, rather than tagging an entire
shared human/agent account indiscriminately.

```python
from agent_activity.sdk import Client, Outbox
import os

client = Client("https://agents.seiche.info/activity", os.environ["AGENT_ACTIVITY_CREDENTIAL"])
queue = Outbox("/private/app/activity-outbox", client)
queue.enqueue(event)  # Validate against /v1/contract; stable immutable event_id.
queue.flush()         # Schedule regularly and inspect pending/rejected status.
```

The JavaScript module is `clients/activity.mjs` with `activity.d.ts`. It performs
explicit bounded HTTPS requests; durable storage is the caller's responsibility.
Do not collect content by wrapping arbitrary MCP calls. Emit only the contract's
selected metadata from an explicitly enabled integration.

`AlpacaReadOnly(oauth_token, paper=True|False)` reads a bounded interval using
`fills(after, until)`. Map each fill with `event(...)` and enqueue it. Keep OAuth
tokens locally, request read-only authorization and do not advance the interval
after a page-budget error. Replaying immutable IDs is safe. This service does not
run an OAuth authorization server or request trading authority.

`merchant_event(...)` accepts only the selected lifecycle metadata after the
merchant has verified its own provider's signed receipt/webhook. It is a normalizer,
not a signature verifier or a proof of payment. The enrolled source authority must
admit the event. A payment source cannot declare fulfillment; an operator cannot
declare a payment settlement. Submit separate records and join them by journey ID.

## Source and rights rules

Public metadata discovery is not permission to resell full source records. Raw
snapshots and dossiers are operator-only; public responses expose bounded metadata,
collection receipts and selected protocol observations. Bulk source resale, shared
training and record-level resale are disabled. An aggregate-resale reference must
represent an actual reviewed grant; the program does not create rights by storing it.

8004scan is an indexer, not cryptographic proof of an agent's behavior. x402 discovery
is a service catalog, not an observer of every transaction. RPC observations trust
the selected provider and its `finalized` tag; this service is not a light client.
Account control, AI control and economic causation require separate evidence.

Primary source references checked 2026-10-14:

- [ERC-8004 specification](https://eips.ethereum.org/EIPS/eip-8004)
- [8004scan developer API](https://8004scan.io/developers)
- [x402 service discovery](https://docs.cdp.coinbase.com/x402/buyer/discover-services)
- [Virtuals ACP overview](https://os.virtuals.io/acp/overview)
- [Hyperliquid info API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Alpaca account activities](https://docs.alpaca.markets/us/reference/getaccountactivities-2)
- [Alpaca OAuth](https://docs.alpaca.markets/us/docs/using-oauth2-and-trading-api)
- [Base public RPC status and limits](https://status.base.org/)

See [operations](ops/RUNBOOK.md), [pilot and commercial validation](PILOT.md),
and [third-party ABI notice](THIRD_PARTY.md).
