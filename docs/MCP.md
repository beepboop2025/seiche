# Seiche as an agent tool (MCP)

Seiche speaks the [Model Context Protocol](https://modelcontextprotocol.io), so
any MCP-capable agent — Claude Code, Codex, or your own — can read the live
funding-stress board the same way a human reads the terminal.

Where a raw data feed hands an agent macro numbers, Seiche hands it the
**conclusion**: a regime read, a forward probability, the nearest historical
analogs, and a historical diagnostic whose status, misses, and eligibility
flags stay attached. It is the judgment layer, exposed as tools.

The server is **stdlib-only** (JSON-RPC 2.0 over stdio) — no new dependencies
beyond what Seiche already installs, and nothing to run but the command you
already have.

## Quick start

From a checkout with the backend installed (`pip install -e backend`):

```bash
seiche mcp        # or: seiche-mcp   — serves on stdio, logs to stderr
```

That's the whole server. Point a client at it.

Want a ready-made agent on top rather than a bare client? The
[Hermes desk-agent kit](../integrations/hermes/) ([guide](HERMES.md)) deploys
[hermes-agent](https://github.com/NousResearch/hermes-agent) against this
server with skills, scheduled briefs, and messaging-platform delivery.

### Claude Code

```bash
claude mcp add seiche -- seiche-mcp
```

or add it to `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "seiche": {
      "command": "seiche-mcp"
    }
  }
}
```

### Codex / generic MCP client

```json
{
  "mcpServers": {
    "seiche": {
      "command": "seiche-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

If `seiche-mcp` isn't on the client's PATH, use the venv's absolute path
(`/path/to/backend/.venv/bin/seiche-mcp`) or `python -m seiche.mcp_server`.

## Hosted endpoint (HTTP) — zero install

The same tools are served over HTTP at **`/mcp`** on the API (so, once deployed,
`https://api.seiche.info/mcp`). An agent adds the URL — nothing to install. It's
the [Streamable HTTP](https://modelcontextprotocol.io) transport in
single-response mode: `POST /mcp` with a JSON-RPC body, JSON-RPC back.

```json
{
  "mcpServers": {
    "seiche": {
      "url": "https://api.seiche.info/mcp",
      "headers": { "Authorization": "Bearer YOUR_TOKEN" }
    }
  }
}
```

- **Anonymous** (no token) → eight tools, named so you can check this against the
  code rather than take it on faith: `funding_stress_now`, `historical_analogs`,
  `proof_backtest`, `data_health`, `crypto_stress_record` and
  `institutional_flows`, plus `oil_funding_context` and
  `fx_materials_passage`. The conclusion, precedent, track record with its
  misses, freshness, crypto transmission record, positioning read, and
  cross-market oil/FX/material context. Capped per IP per day. Zero setup, and
  it stays free.
- **Subscriber** (bearer token) → the same eight plus the five that read the
  derived engines: `funding_stress_forecast`, `replay_asof`, `positioning_book`,
  `desk_brief`, `ask_desk`. At your tier's quota.

`tools/list` returns exactly what the caller can run, so an anonymous agent
never sees a tool it would be refused on. The list is generated from the
`is_public` flag on each entry in `TOOLS` (`backend/seiche/mcp_server.py`);
that flag is the boundary, and this page is downstream of it.

The endpoint lives on the existing FastAPI app behind the same Caddy reverse
proxy as the rest of the API — no separate service to run or deploy.

The two cross-market contracts are also available as compact anonymous REST
reads when an integration does not speak MCP:

```bash
curl https://api.seiche.info/api/oil-funding
curl https://api.seiche.info/api/estuary
```

Those payloads are the same chartless contracts returned by
`oil_funding_context` and `fx_materials_passage`; the oil contract also carries
Ballast plus a chartless market-structure block (live Cushing stocks and
Brent−WTI spread separated from dated capacity and chokepoint references).
Telegram and MCP therefore do
not maintain separate interpretations of the engines.

### Getting a token

```bash
seiche user add desk_01 --tier pro          # operator provisions the account
curl -sX POST https://api.seiche.info/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"desk_01","password":"…"}'  # returns a 30-day bearer token
```

### Metering

Every tool call is metered per caller per UTC day and reported in response
headers:

| Header | Meaning |
|--------|---------|
| `X-MCP-Usage-Used` | tool calls used today |
| `X-MCP-Usage-Limit` | the daily quota (absent when unlimited) |
| `X-MCP-Usage-Remaining` | calls left today |

Check the meter any time: `GET /mcp/usage`. When the daily quota is reached, a
`tools/call` returns an `isError` result pointing at the upgrade page — the
agent can relay it. Only `tools/call` is billable; `initialize`, `tools/list`,
and `ping` are free.

Quotas are the commercial dials, tuned in `backend/seiche/config.py`
(`MCP_DAILY_QUOTAS`, `MCP_ANON_DAILY`, `MCP_RATE_LIMIT_PER_MIN`): anonymous
callers get a small daily cap, `pro` a working quota, `founder`/`enterprise`
unlimited.

### Closing the funnel: payment → account

A confirmed payment becomes a subscriber account and bearer token via
`provision()` — either the operator CLI (the manual crypto path) or a signed
webhook (a payment processor: BTCPay, NOWPayments, Stripe, …).

Operator path, after you see a crypto payment land:

```bash
seiche provision --tier pro --email buyer@x.com --ref <txid>
# prints username + password (once) + a 30-day token; emails them if SMTP is set
```

Webhook path — enable it by setting a shared secret, then have the processor
(or a tiny adapter) POST a signed JSON body:

```bash
export SEICHE_PROVISION_SECRET=…            # fail-closed: no secret => 503
```

```
POST /api/provision
X-Seiche-Signature: <hex HMAC-SHA256 of the raw body, keyed by the secret>

{ "tier": "pro", "email": "buyer@x.com", "payment_ref": "invoice_123",
  "amount": 29, "currency": "USD" }
```

The call is **idempotent on `payment_ref`** — a retried webhook never
double-grants and never re-issues a password. On first grant it returns the
credentials (`username`, `password` shown once, `token`); on a replay it returns
the recorded account with `"already": true`. `payment_ref` and every grant are
recorded in the `provisions` table for audit.

## Tools

| Tool | What it answers | Surface |
|------|-----------------|---------|
| `funding_stress_now` | Current 0–100 stress index, regime, per-component decomposition, the Tell | public |
| `historical_analogs` | The most similar past days + how often they led to a stress event, with a novelty flag | public |
| `proof_backtest` | Recall/precision with 95% CIs, orthogonal test, every episode incl. misses | public |
| `data_health` | Freshness, provenance, and fault status for every input series | public |
| `crypto_stress_record` | Wrecks: labelled crypto stress episodes (Terra, FTX, SVB/USDC, the Oct-2025 cascade…) replayed point-in-time against the funding board — transmission vs specificity, stated honestly | public |
| `institutional_flows` | Hedge-fund, pension and sovereign positioning from public prints; implementation version tags withheld anonymously | public |
| `oil_funding_context` | WTI/Brent and funding evidence; Ballast's CFTC WTI/Henry Hub gross cash-displacement, concentration and EIA inventory ledger; live Cushing/Brent−WTI observations separated from dated capacity, benchmark and chokepoint references; change-on-change coupling; explicitly scenario-only cargo/margin/India arithmetic | public |
| `fx_materials_passage` | Upstream FX/material pressure versus funding priced, with the Passage's discovery/holdout ledger and settlement scenarios | public |
| `funding_stress_forecast` | P(funding event) at 5/10/21bd from three independent models, each validated | subscriber |
| `replay_asof` | The Time Machine: the whole board reconstructed point-in-time on a past date (`date: YYYY-MM-DD`) | subscriber |
| `desk_brief` | Today's full desk note as markdown | subscriber |
| `positioning_book` | Implied stance + positions, walk-forward Sharpe, live record | subscriber |
| `ask_desk` | Natural-language Q&A grounded strictly in the live board (needs an LLM endpoint) | subscriber |

The free tier gives the **conclusion, credibility, and contextual transmission
read** (regime, analogs, PROOF, data health, positioning, oil, FX and materials)
— enough to be genuinely useful and to spread.
The **edge** (forward odds, the Time Machine, positioning, the assistant) is the
subscription. The split is one `is_public` flag per tool in `mcp_server.py`.

Every tool returns structured JSON (or markdown, for `desk_brief`) with a short
`reading` field that tells the agent how to interpret the numbers.

## Machine-native support (x402) — dormant by design

Seiche is a free public good funded by grants and voluntary support
(seiche.info/support), not a subscription product. The codebase also
carries a dormant [x402](https://docs.cdp.coinbase.com/x402/welcome) rail:
if it is ever enabled, an AI agent with a wallet can voluntarily chip in a
few cents of USDC per call for the operator-cost tools — support in the
currency agents hold, not a paywall, and never a condition for the public
surface, which stays free forever.

The rail is **off by default** and fail-closed (it only exists when the
operator sets `SEICHE_X402_PAY_TO`; decode/verify/settle failures serve
nothing). Amounts live in `backend/seiche/config.py` (`X402_PRICES_USD`);
network and facilitator are env dials (`SEICHE_X402_NETWORK`,
`SEICHE_X402_FACILITATOR`). When on, the anonymous `tools/list` says which
tools accept support and how much; a `tools/call` carrying a valid
`X-PAYMENT` header runs on the full surface, with the settlement receipt
returned in `X-PAYMENT-RESPONSE`.

## Public vs. full surface

Set `SEICHE_MCP_PUBLIC=1` to expose only the free tools over **stdio**. This is
the same eight the hosted endpoint gives an anonymous caller, so a local run and a
no-token HTTP call see the same surface:

```bash
SEICHE_MCP_PUBLIC=1 seiche-mcp
```

| tool | public | why |
|---|---|---|
| `funding_stress_now` | yes | the conclusion, which is the free good |
| `historical_analogs` | yes | precedent from the public record |
| `proof_backtest` | yes | the track record, misses included |
| `data_health` | yes | you should be able to check freshness before trusting a number |
| `crypto_stress_record` | yes | labelled episodes replayed against the board |
| `institutional_flows` | yes | public prints in, a reading out (`method_versions` withheld) |
| `oil_funding_context` | yes | compact observed transmission, Ballast futures-cash context and live-vs-reference oil-market structure; bounded derivations and scenarios stay labelled and separate |
| `fx_materials_passage` | yes | compact upstream gap plus the untouched-holdout ledger |
| `funding_stress_forecast` | no | six modelled views of forward event odds |
| `replay_asof` | no | full point-in-time board reconstruction |
| `positioning_book` | no | sleeves, weights, `p_ensemble`, tcost |
| `desk_brief` | no | the whole board as prose, with driver weights |
| `ask_desk` | no | runs the operator's LLM budget |

The rule behind the column: what Seiche gives away is the **conclusion**; what
it keeps is the **gated engine that produced a forecast, replay, position or
LLM answer**. That is why `institutional_flows` is public but drops its
`method_versions`, while the two cross-market tools return chartless contextual
views and keep scenario arithmetic visibly separate from observations.

`is_public` on each `TOOLS` entry in `backend/seiche/mcp_server.py` is the one
place this is decided. Before commit `82d5700` the HTTP layer disagreed with it
(`public = ident is None and _board_gate_enabled()` tied MCP entitlements to a
setting about the browser board, so with the gate off, which is the shipped
default, every anonymous caller ran on the full surface). An anonymous caller
is now always the public surface. If you change `is_public`, change this table.

## Notes

- The server assembles the board on the first tool call and caches it for five
  minutes, so a burst of tool calls in one agent turn shares a single fetch.
- All output is point-in-time; `replay_asof` never looks ahead of its date.
- Stray backend logging is redirected to stderr — stdout carries only the
  JSON-RPC protocol stream, so the transport stays clean.
- Not investment advice. Every reading is backed by the PROOF scoreboard —
  agents are instructed to cite it.
