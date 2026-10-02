# Seiche Evidence Dossier · Algorand x402

An isolated research integration for the Algorand Global x402 Challenge. One
`GET /v1/dossier` purchases a normalized evidence package for **0.01 USDC**.
The service reads existing public Seiche, Undertow and LiquiLens APIs in parallel,
preserves their independent evidence clocks and limitations, and produces
per-section and whole-payload SHA-256 integrity fingerprints.

**Implementation is not competition eligibility.** A receiver, public deployment,
Testnet end-to-end evidence, real Mainnet settlement and receipt, USDC receipt,
Bazaar/leaderboard discovery, demo and actual submission must all be verified.
No on-chain payment has been executed by the test suite or this integration kit.

## What the purchase adds

- A bounded, stable `seiche.evidence-dossier.v1` JSON contract across products
- Separate source publication, observation, knowledge and capture clocks
- Source-response and section fingerprints for downstream change detection
- Explicit unavailable/stale/withheld sections and original validation blockers
- Money-market observations only when redistribution is explicitly allowed,
  values are finite, and source clocks/status pass the documented local gates
- Pre-payment availability inspection; required funding, money-market and
  liquidity evidence must exist before verification or settlement is attempted

This is evidence assembly over publicly accessible source material. It is not a
claim of exclusive data, a joint risk score, causal attribution, a forecasting
track record, provider-authenticated attestation or authority to execute trades.
Partial institutional coverage is disclosed before payment, not silently filled.

## Public routes

| Route | Purpose |
| --- | --- |
| `/` | Working coverage and payment-requirement inspector |
| `/health` | Configuration status; never settlement/eligibility proof |
| `/v1/preview` | Free availability, source clocks, limitations and price |
| `/v1/dossier` | x402 v2 paid dossier; no query parameters accepted |
| `/openapi.json` | Machine contract and declared source URLs |

The Bazaar extension describes the response. Every payment offer carries
`extra.tag: x402-global-challenge` before settlement. A real GoPlausible
settlement against the public endpoint triggers discovery; metadata alone does
not establish a listing. Use one Mainnet receiver and one root domain throughout
the competition. Do not create artificial volume or describe tests as customers.

## Run and validate

Requires Node 22+ (tested on Node 24).

```sh
npm ci
npm test
npm run typecheck
npm run build
npm run preflight
```

`preflight` only reads public evidence, facilitator support and the configured
public account's USDC opt-in. It does not sign or submit transactions. Set the
three public environment variables shown in `config.example.env` to run that check.
No service private key, mnemonic or facilitator credential is needed.

The entrypoint is `src/worker.ts`, compiled to `dist/server/index.js`, exporting
Cloudflare Workers `fetch`. Enable `nodejs_compat` (required by SDK Buffer and URL
uses). Constructing payment middleware is lazy and request-scoped. Do not run
network initialization at Worker module scope. `npm run build` emits a portable
ESM bundle; this kit does not change Seiche's production code or release gates.

## Payment boundary

- Dormant without an explicit network, checksum-valid nonzero public receiver
  and canonical HTTPS origin; incomplete configuration returns 503
- Fixed GoPlausible facilitator; exact network, asset, amount, receiver and
  resource bound to the advertised payment; canonical/legacy CAIP2 aliases are
  selected only from the facilitator's actual `/supported` response
- USDC Mainnet ASA 31566704 or Testnet ASA 10458941; six decimal places
- No credentials, custody, identity grants, downstream paid purchases or trades
- Hono verifies before the pure handler, buffers the dossier, and releases it
  only after successful settlement; failed or malformed receipts withhold it
- Settlement responses require the expected network and a 52-character Algorand
  transaction ID; optional payer must be a checksum-valid address
- Never automatically retry a payment after timeout: a settlement outcome may
  be indeterminate, requiring wallet/transaction reconciliation
- Both modern and legacy payment headers are bounded; response caching disabled
- SDK/facilitator errors are sanitized before public serialization

The tests use a deterministic public-key fixture and mock facilitator only.
No private key exists in the fixtures and no real chain is contacted.

## Sources, clocks and rights

| Section | Canonical source | Clock/rights and failure boundary |
| --- | --- | --- |
| Funding | https://api.seiche.info/api/public | Public derived projection; preserve proof-withheld/current-vintage limits, editorial source dates and source-staleness counts |
| Risk context | https://api.seiche.info/api/trade-safety/risk-context | Public metadata-only/context projection; retain retired-source disclosures, original evidence clock, no execution eligibility and unevaluated attestation |
| Money markets | https://api.seiche.info/api/v2/money-markets | Compact only explicit `redistribution_status=allowed` observations with finite values and valid nonfuture source clocks; no raw histories, licensed benchmark values or inferred/forward-filled values |
| Liquidity | https://api.seiche.info/undertow/x402/summary | Already-public observation subset; preserve per-segment withheld tiers, failed validations, observation dates and full-fidelity paid source link |
| Institutions | https://api.liquilens.in/api/failure-radar/board | Uses the public `as_of` board clock; only India failure-radar aggregate tiers/row count and historical-evidence limits when schema/clock valid; no institution financial rows; 403 stays unavailable with no alternate-access attempt |

Source adapters use fixed allowlisted URLs, reject redirects, cap streamed bodies
at 1 MiB each and time out after 12 seconds. Successful assembled responses are
cached in memory for at most 30 seconds, with one in-flight assembly per isolate.
Seiche generated snapshots older than two hours are withheld; date-only daily
product snapshots older than two days are withheld. These are transport snapshot
gates, not a replacement for native source-publication freshness policies.
Unknown/future source clocks never gain freshness from a recent fetch. Source
rights and caveats remain applicable; the code license does not override them.
A fingerprint proves byte equality only, not publisher identity or truth.

## Activation and rollback

1. Deploy an isolated public service with payment configuration unset. Verify
   preview, health and disabled 503 behavior; keep established product runtime
   and signed release controllers unchanged
2. Have the owner supply an existing public Testnet receiver opted into Testnet
   USDC and complete the Testnet payment personally, with funded wallet tools
3. Verify the actual paid dossier/receipt and Bazaar schema from the deployed
   runtime; a local mock is insufficient
4. Switch deliberately to a Mainnet receiver opted into Mainnet USDC, preserving
   the same project/root-domain receiver for the competition. No secret belongs
   in chat, repository or server configuration
5. Owner completes at least one real Mainnet purchase; record transaction ID,
   receiver USDC receipt, real paid response, Bazaar and leaderboard evidence
6. Submit only verified facts and the actual 3–5 minute public demo/repository

Rollback: remove payment configuration to fail closed, or redeploy the previous
isolated service version. This never rolls back or changes existing Seiche,
LiquiLens or Undertow production releases. Do not remove a deployed endpoint
while a payment outcome is unresolved; reconcile that transaction first.

## Primary implementation references

- https://dev.algorand.co/resources/x402-on-algorand/
- https://github.com/algorandfoundation/x402-demo/tree/main/x402-basic-tutorial/server
- https://github.com/x402-foundation/x402/blob/main/specs/schemes/exact/scheme_exact_algo.md
- https://github.com/GoPlausible/.github/tree/main/profile/algorand-x402-documentation
- https://algorand.co/blog/the-x402-global-challenge-is-live-how-to-build-submit-your-entry

This isolated kit is AGPL-3.0-or-later, matching the parent repository.
