# Seiche as an agent tool (MCP)

Seiche speaks the [Model Context Protocol](https://modelcontextprotocol.io), so
any MCP-capable agent — Claude Code, Codex, or your own — can read the live
funding-stress board the same way a human reads the terminal.

Where a raw data feed hands an agent macro numbers, Seiche hands it the
**conclusion**: a regime read, a forward probability, the nearest historical
analogs, and an honest backtest. It is the judgment layer, exposed as tools.

The server is **stdlib-only** (JSON-RPC 2.0 over stdio) — no new dependencies
beyond what Seiche already installs, and nothing to run but the command you
already have.

## Quick start

From a checkout with the backend installed (`pip install -e backend`):

```bash
seiche mcp        # or: seiche-mcp   — serves on stdio, logs to stderr
```

That's the whole server. Point a client at it.

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

- **Anonymous** (no token) → the free public surface (the conclusion, historical
  analogs, the PROOF scoreboard, data health), capped per IP per day. Try it with
  zero setup.
- **Subscriber** (bearer token) → the full surface at your tier's quota.

The endpoint lives on the existing FastAPI app behind the same Caddy reverse
proxy as the rest of the API — no separate service to run or deploy.

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
| `funding_stress_forecast` | P(funding event) at 5/10/21bd from three independent models, each validated | subscriber |
| `replay_asof` | The Time Machine: the whole board reconstructed point-in-time on a past date (`date: YYYY-MM-DD`) | subscriber |
| `desk_brief` | Today's full desk note as markdown | subscriber |
| `positioning_book` | Implied stance + positions, walk-forward Sharpe, live record | subscriber |
| `ask_desk` | Natural-language Q&A grounded strictly in the live board (needs an LLM endpoint) | subscriber |

The free tier gives the **conclusion and the credibility** (regime, analogs, the
PROOF scoreboard, data health) — enough to be genuinely useful and to spread.
The **edge** (forward odds, the Time Machine, positioning, the assistant) is the
subscription. The split is one `is_public` flag per tool in `mcp_server.py`.

Every tool returns structured JSON (or markdown, for `desk_brief`) with a short
`reading` field that tells the agent how to interpret the numbers.

## Public vs. full surface

Set `SEICHE_MCP_PUBLIC=1` to expose only the free tools — the conclusion, the
historical analogs, the PROOF scoreboard, and data health. This mirrors the
anonymous `/api/public` surface and is the mode a **hosted, no-auth endpoint**
runs so agent-builders can try Seiche with zero friction:

```bash
SEICHE_MCP_PUBLIC=1 seiche-mcp
```

The `positioning_book` and `ask_desk` tools are hidden in public mode.

## Notes

- The server assembles the board on the first tool call and caches it for five
  minutes, so a burst of tool calls in one agent turn shares a single fetch.
- All output is point-in-time; `replay_asof` never looks ahead of its date.
- Stray backend logging is redirected to stderr — stdout carries only the
  JSON-RPC protocol stream, so the transport stays clean.
- Not investment advice. Every reading is backed by the PROOF scoreboard —
  agents are instructed to cite it.
