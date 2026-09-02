# Seiche × Hermes: the desk agent kit

Turn [hermes-agent](https://github.com/NousResearch/hermes-agent) into a
funding-stress desk agent that lives in your Telegram (or Discord, Slack,
Signal, WhatsApp), reads the Seiche board through the existing MCP server,
sends a grounded morning brief, alerts on regime changes, audits its own
track-record claims, and watches deployment health. No new backend code:
the kit is skills, a persona, config fragments, and one bootstrap
conversation.

Full operator guide: [docs/HERMES.md](../../docs/HERMES.md).

## What's in the kit

| Path | What it is |
|---|---|
| `skills/seiche-desk-brief/` | Compose the chat-sized desk note (tool order, format, discipline) |
| `skills/seiche-regime-watch/` | Alert policy: triggers, anti-noise rules, silent passes |
| `skills/seiche-time-machine/` | Point-in-time episode replay with no-lookahead discipline |
| `skills/seiche-proof-audit/` | The trust question, answered from the PROOF scoreboard |
| `skills/seiche-ops-watchdog/` | Data-health passes, amber/red classification, escalation format |
| `skills/seiche-harbors-watch/` | Source-clock-aware world money, FX, capital-market and China-context watch |
| `skills/seiche-crypto-scout/` | Weekly crypto x money-market recon: transmission, gaps, revenue opportunities |
| `AGENTS.md` | The desk-agent persona and hard rules (grounding, advice disclaimer, PIT) |
| `config.example.yaml` | Hermes config fragments: MCP wiring (3 options), provider, gateway |
| `env.example` | The secrets the deployment needs |
| `BOOTSTRAP.md` | First message to send: self-verify, seed memory, create cron jobs |
| `install.sh` | Copies skills + persona into `~/.hermes`, prints the manual steps |

## Quick start

```bash
# 1. Install hermes (their installer)
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# 2. Install this kit
./install.sh

# 3. Wire config + secrets (printed by install.sh), then
hermes gateway

# 4. Paste BOOTSTRAP.md's message to the bot
```

Works against any of the three Seiche MCP wirings: local stdio
(`seiche-mcp`), same-box HTTP (`127.0.0.1:8787/mcp`), or the hosted endpoint
(`https://api.seiche.info/mcp`, anonymous free tier or subscriber token).

## Anonymous tool contract

The hosted endpoint exposes these twelve tools without a token. The list is
tested against the server registry so a runtime change cannot leave this kit
silently teaching an obsolete surface.

| Tool | Hermes uses it for |
|---|---|
| `latest_article` | Exact published editorial, evidence clock, and publication receipt |
| `funding_stress_now` | Current regime, composite, decomposition, and Tell |
| `historical_analogs` | Nearest point-in-time precedents and novelty |
| `proof_backtest` | Historical diagnostic status, misses, and eligibility |
| `data_health` | Freshness, provenance, and faults before interpretation |
| `crypto_stress_record` | Labelled crypto episodes against the funding board |
| `institutional_flows` | Public positioning and flow context |
| `money_market_context` | Bounded USD desk sections, sources, and methodology |
| `world_markets_context` | Money, FX, capital-market, and rights-safe China context |
| `trade_safety_risk_context` | Cache-only regime/index/coverage/clocks for non-executable Trade Safety guards |
| `oil_funding_context` | Oil/funding observations, market structure, and separate scenarios |
| `fx_materials_passage` | FX/material pressure and holdout-tested Passage links |

The five subscriber analysis tools remain `funding_stress_forecast`, `replay_asof`,
`desk_brief`, `positioning_book`, and `ask_desk`. Hermes must treat an auth or
quota refusal as an access condition, not as evidence that the public board is
down.

An authenticated hosted connection also discovers five private Agent Room
preview tools. They are non-executable signed discussion primitives, not a
trading connector; follow [`docs/AGENT-ROOM.md`](../../docs/AGENT-ROOM.md) and
never turn an acknowledgement into acceptance or execution.

Not investment advice; every reading the agent relays is backed by the
public PROOF scoreboard, misses included.
