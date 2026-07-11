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

Not investment advice; every reading the agent relays is backed by the
public PROOF scoreboard, misses included.
