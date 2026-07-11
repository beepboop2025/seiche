# Running Seiche under a Hermes agent

Seiche's [MCP server](MCP.md) hands any agent the board's conclusions as
tools. [hermes-agent](https://github.com/NousResearch/hermes-agent) (Nous
Research, MIT) is the best open home for those tools today: it is
self-hosted, provider-agnostic, reachable from Telegram/Discord/Slack/
Signal/WhatsApp through one gateway, has a built-in cron scheduler that
delivers to those platforms, and keeps a learning loop (skills it can
extend, memory it curates) so the desk agent gets better with use.

The result is a **funding-stress desk agent**: a bot in your pocket that
sends a grounded morning brief, stays silent until a regime trigger fires,
replays history point-in-time on request, and answers "can I trust this"
from the PROOF scoreboard with the misses shown.

Everything ships in [`integrations/hermes/`](../integrations/hermes/). This
page is the operator walkthrough.

## Why an agent instead of another cron script

The `seiche watch` loop already emails alerts. The agent layer adds what a
script cannot:

- **Judgment on demand.** "Is today like September 2019?" is a conversation
  over `replay_asof` and the decomposition, not a canned report.
- **Adaptive noise control.** "Only bother me at STRAIN or worse" is one
  sentence; the agent updates its own watch policy and remembers it.
- **A learning loop.** Hermes persists procedures as skills and facts as
  memory. The kit seeds six skills; the agent refines them from real use.
- **Distribution.** Anyone can point a Hermes at the hosted endpoint's free
  tier and get a working desk agent in minutes; the subscriber token
  upgrades it in place. The kit is a funnel, not just tooling.

## Setup

### 1. Install Hermes

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup        # pick a provider; any strong model works
```

### 2. Install the kit

```bash
cd integrations/hermes && ./install.sh
```

This copies the six `seiche-*` skills into `~/.hermes/skills/seiche/` and,
if you don't already have one, installs the desk-agent persona as
`~/.hermes/AGENTS.md`. It never edits config or secrets.

### 3. Wire the MCP server

Merge the `mcp_servers` block from
[`config.example.yaml`](../integrations/hermes/config.example.yaml) into
`~/.hermes/config.yaml`. Three options:

| Wiring | Config | Surface |
|---|---|---|
| Local stdio | `command: seiche-mcp` | Full (or `SEICHE_MCP_PUBLIC=1` for free tools) |
| Same box as the API | `url: http://127.0.0.1:8787/mcp` | Per token |
| Hosted | `url: https://api.seiche.info/mcp` | Anonymous free tier, or per token |

Then make the toolset available to your platform (`platform_toolsets:` in
the same file) and confirm with `hermes tools` that the seiche tools
appear.

### 4. Gateway

Put `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, and
`TELEGRAM_HOME_CHANNEL` in `~/.hermes/.env` (see
[`env.example`](../integrations/hermes/env.example)), enable the platform in
config, and run:

```bash
hermes gateway
```

Lock `TELEGRAM_ALLOWED_USERS` down before wiring a subscriber token: an
open bot with subscriber tools is your quota and your data, answering
strangers.

### 5. Bootstrap

Send the message in
[`BOOTSTRAP.md`](../integrations/hermes/BOOTSTRAP.md). The agent verifies
its own wiring, seeds memory (pull times, watch baseline, your channel),
creates five scheduled jobs (morning brief 12:35 UTC, evening watch 21:05
UTC, weekly PROOF report, daily ops check, weekly crypto scout), and runs
the brief once so you see the format. From then on you manage everything conversationally.

## What each skill encodes

| Skill | Lens |
|---|---|
| `seiche-desk-brief` | The note: `data_health` first, then index/regime, forward odds, analogs; chat-sized format; PROOF citation and advice disclaimer every time |
| `seiche-regime-watch` | The pager: six triggers (regime change, 10-point jump, model agreement, Tell divergence, novelty, data fault), one alert per trigger per day, silent pass = success |
| `seiche-time-machine` | The historian: episode walks at -21/-10/-5 days, then-vs-now by component, claim checking via replay instead of hindsight |
| `seiche-proof-audit` | The skeptic's answer: recall with CI and N, the orthogonal test, misses by name, the notary ledger as the tamper-evidence fact |
| `seiche-ops-watchdog` | The pager for the operator: green/amber/red on `data_health`, PIT-gap is always red, escalation format, report-don't-repair boundary |
| `seiche-crypto-scout` | The frontier watch: weekly crypto x money-market pass (stress transmission, tool gaps, grant deadlines, agent-payment rails) with a running ledger |

The persona (`AGENTS.md`) carries the hard rules the skills assume: grounded
or silent, fail loud on bad data, point-in-time discipline, quota awareness,
and "Not investment advice." on every reading.

## Costs and metering

Every MCP `tools/call` is metered per caller per UTC day (see
[MCP.md](MCP.md)). The kit's daily rhythm is roughly 12-15 tool calls
(brief ~5, watch ~2 silent / ~4 firing, ops ~1, weekly audit ~2), well
inside the `pro` quota with room for conversation. LLM inference cost sits
with whatever provider you configured in Hermes: bring your own key, use a
local model, or Nous Portal.

## Security notes

- Prefer running Hermes as its own OS user; on the same box as the API it
  only needs to reach `127.0.0.1:8787`.
- Keep Hermes command approval on for shell tools; the desk agent's job
  needs only the MCP toolset, so a minimal `platform_toolsets` list is the
  cheapest hardening.
- `skills.guard_agent_created: true` safety-scans skills the agent writes
  for itself as the learning loop runs.
- The bot token and any subscriber token live in `~/.hermes/.env`, never in
  config committed anywhere.
