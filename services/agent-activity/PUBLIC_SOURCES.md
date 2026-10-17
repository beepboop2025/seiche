# Building the public evidence product before partnerships

Reviewed 14 October 2026. Public-first collection can start before negotiated
platform partnerships. Ordinary API terms, infrastructure costs, operator consent
and the right to redistribute data remain separate questions.

## What we can collect

| Route | Obtainable evidence | Attribution boundary | Current implementation |
| --- | --- | --- | --- |
| Olas marketplace contract on Gnosis | Finalized marketplace request and delivery event logs, transaction references, chain timestamps and source receipts | Protocol association, not independently verified AI authorship, service quality or unique buyers | Public starter RPC collector; no credentials or partnership |
| Virtuals ACP on Base | Selected contract job events | A protocol event is not verified revenue or AI authorship | Collector exists; public Base endpoint currently returns 429 |
| ERC-8004 registries | Registered identities and declared services; wallet linkage when supported by the deployed registry | Registration and wallet control do not prove autonomous trading | Rolling registry metadata sampling; wallet attribution enrichment is future work |
| x402 discovery | Paid-service listings and facilitator-reported activity | A service or payer is not necessarily an agent; listings are not purchase receipts | Rolling catalog sampling |
| Hyperliquid public API | Public market trades, and documented account fills for an identified public account | A trader is not automatically an AI agent; API agent wallets can mean signing delegates | Existing reviewed-account collector; market-read feasibility checked separately |
| Polymarket public Data API | Public trade records and account activity | No inherent AI identifier; version, history and reuse limits need source-specific review | Five-record read verified; not admitted to recurring production collection |
| Opt-in runtime SDK | Agent/version, action references and outcome receipts supplied directly by participating operators | Operator assertions require independent matching; separate purpose and resale permissions | Existing Python encrypted outbox and JavaScript client; real contributors still required |
| Our own evidence API | Content-free request metadata and attributable enrolled-client receipts | Tool access is evidence of access, not a downstream trade or purchase | Existing platform telemetry is separate from economic outcomes |

The Olas contract configuration in the maintained repository supersedes legacy
addresses still present in some documentation. Source review must pin both the
contract and event definitions, rather than copying an address from an old example.

## Open-source components and their role

- The existing collector, encrypted contribution SDK, SQLite evidence store and
  dashboard are in this repository under AGPL-3.0-or-later.
- [web3.py](https://github.com/ApeWorX/web3.py) provides MIT-licensed Ethereum
  interfaces. [Ponder](https://github.com/ponder-sh/ponder) is an open-source
  indexing option if we later need a larger indexed application. Neither gives
  private account access or creates a data license. The initial collector can
  use ordinary HTTP and pinned ABI material without adding these dependencies.
- [Olas marketplace contracts](https://github.com/valory-xyz/autonolas-marketplace)
  provide the event definitions and current deployment configuration. Its MIT
  notice is retained in `THIRD_PARTY.md`.
- [Running our own Base node](https://docs.base.org/specifications/node-operators/run-a-node)
  reduces dependence on a hosted RPC provider. It needs substantial storage,
  reliable connectivity and maintenance; it is not a zero-cost shortcut. Base's
  [hardware guidance](https://docs.base.org/specifications/node-operators/performance-tuning)
  recommends 8+ CPU cores, at least 32 GB RAM and local NVMe storage sized for
  current chain data and restoration headroom. No node has been provisioned.
- A self-service RPC account can be obtained without a strategic data partnership.
  [Coinbase Node](https://docs.cdp.coinbase.com/data/node/overview) documents a free
  allowance with metered use beyond it. Availability, budget and terms must be
  checked when provisioning. No account, payment plan or API key was created.

Open-source licensing covers software. Public API availability, collection
permission, attribution, publication and commercial redistribution are evaluated
independently. Record-level resale and shared training remain off.

## Measurement rules

1. Keep discovery records, native event logs, deduplicated jobs, account activity
   and independently verified outcomes as different measurements.
2. Start with protocol association. Upgrade attribution only with a defensible
   agent/version/account/time link. ERC-8004's `agentWallet` can establish a
   declared receiving wallet; its signature mechanism establishes wallet control,
   not that every transaction is AI-generated. Transfers can reset that linkage.
3. Keep a transaction hash, log index, chain, contract, block hash, event time,
   capture time, source revision and receipt. Do not silently advance a cursor
   after a failed or partial window.
4. Treat a protocol delivery acknowledgement as a protocol assertion. Payment
   settlement, refunds, physical fulfillment and independent quality verification
   require additional evidence. Do not add requests and deliveries together as
   a count of completed jobs or revenue.
5. Report coverage only for named contracts, topics, accounts and time windows.
   The worldwide population and our share of it remain unknown.

## Practical rollout

First collect the Olas public event history prospectively and verify automatic
recurrence. Continue existing registry and service discovery. Then obtain an
ordinary Base RPC connection, or provision a suitable node, to activate ACP at a
reliable cadence. Do not repeatedly rotate public endpoints around errors.

Next add reviewed account links and consented runtime instrumentation. Independent
operators can opt in directly through a standard participation flow; a negotiated
agreement with their broker or agent-platform vendor is not always necessary to
access their own authorized records. Platform API rules still apply.

Expand to other public protocols only after pinning current deployments, reading
the applicable API terms and proving data quality. Use a narrow useful product:
protocol activity history, source coverage, attributed agent dossiers where
evidence supports them, and reproducible research extracts with clear rights.
Do not wait for a global census or promise a worldwide majority.

Commercial redistribution, institutional qualification and model training require
their own acceptance decisions. This public pilot does not activate those gates.

## Primary references

- [Gnosis public starter RPC](https://docs.gnosischain.com/tools/RPC%20Providers/)
- [Pinned Olas deployment configuration](https://github.com/valory-xyz/autonolas-marketplace/blob/a9398109ec939edb3d1e6aed15f8ced26d0258a0/docs/configuration.json)
- [ERC-8004](https://eips.ethereum.org/EIPS/eip-8004)
- [x402 discovery](https://docs.cdp.coinbase.com/x402/buyer/discover-services)
- [Hyperliquid information API](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint)
- [Polymarket public trades](https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets)
- [PublicNode terms](https://www.publicnode.com/terms)

The research probes returned five Polymarket records and ten Hyperliquid market
trades without credentials. They were connectivity/schema checks, not verified
AI-agent cohorts, and their raw response records were not published. PublicNode
answered two small Base log windows with zero matching ACP logs; a larger request
returned 403. This establishes neither an empty protocol nor reliable free bulk
Base access. The new Gnosis collector has separate live verification evidence.
