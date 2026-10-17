# Public source coverage and attribution

Public-source availability, operator consent and redistribution rights are
separate requirements. The following records retain their stated coverage and
attribution limits.

## Source evidence and limits

| Route | Obtainable evidence | Attribution boundary | Current implementation |
| --- | --- | --- | --- |
| Olas marketplace contract on Gnosis | Finalized marketplace request and delivery event logs, transaction references, chain timestamps and source receipts | Protocol association, not independently verified AI authorship, service quality or unique buyers | Public starter RPC collector; no credentials or partnership |
| Virtuals ACP on Base | Selected contract job events | A protocol event is not verified revenue or AI authorship | Collector exists; public Base endpoint currently returns 429 |
| ERC-8004 registries | Registered identities and declared services; wallet linkage when supported by the deployed registry | Registration and wallet control do not prove autonomous trading | Rolling registry metadata sampling; independent wallet attribution is not established |
| x402 discovery | Paid-service listings and facilitator-reported activity | A service or payer is not necessarily an agent; listings are not purchase receipts | Rolling catalog sampling |
| Hyperliquid public API | Public market trades, and documented account fills for an identified public account | A trader is not automatically an AI agent; API agent wallets can mean signing delegates | Existing reviewed-account collector; market-read feasibility checked separately |
| Polymarket public Data API | Public trade records and account activity | No inherent AI identifier; version, history and reuse limits need source-specific review | Five-record read verified; not admitted to recurring production collection |
| Opt-in runtime SDK | Agent/version, action references and outcome receipts supplied directly by participating operators | Operator assertions require independent matching; separate purpose and resale permissions | Existing Python encrypted outbox and JavaScript client; real contributors still required |
| Our own evidence API | Content-free request metadata and attributable enrolled-client receipts | Tool access is evidence of access, not a downstream trade or purchase | Existing platform telemetry is separate from economic outcomes |

The Olas contract configuration in the maintained repository supersedes legacy
addresses still present in some documentation. Source review must pin both the
contract and event definitions, rather than copying an address from an old example.

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
