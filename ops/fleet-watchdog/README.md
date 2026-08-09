# fleet-watchdog

Out-of-band health probe for every Telegram bot in the fleet, on the Hetzner box.

## Why

Every bot here has a failure mode where **the process stays alive and systemd
still reports `active`**: a revoked token, a 409 from a second poller, a wedged
handler, a webhook someone set. Two bots sat silently dead for weeks that way —
the discovery mechanism was "message the bot, notice the silence".

systemd could not have caught it either. Every unit shipped with
`StartLimitIntervalSec=10s` alongside `RestartSec=10s`, which makes the
5-restart burst mathematically unreachable, so no unit could ever enter
`failed`, so the existing `OnFailure=` alerts were dead code and the deliberate
`sys.exit` on a 401 was a no-op alarm. The bots also block-buffer stdout, so 30
days of journal held zero startup banners — nothing to scrape.

## What it checks, every 5 minutes

| signal | catches |
|---|---|
| `getMe` | token revoked or invalid |
| `getWebhookInfo.pending_update_count` | alive but not consuming its queue |
| `getWebhookInfo.url` | a webhook is stealing updates from the poller |
| state-file mtime (`offset.json`) | the poll loop stopped turning |
| `systemctl is-active` | the unit died outright |
| JSON-RPC `initialize` on each MCP remote | the remote is up but not speaking MCP, or the path stopped routing to it |
| Mac heartbeat mtime + exact `nyx=1` line | the NYX host stopped checking in, or checked in while the bridge was down |

Both Telegram methods are read-only and neither touches `getUpdates`, so
probing can **never** conflict with a running poller. Verified against all
tokens before rollout.

## The MCP remotes, and why they are probed the same way

The hosted MCP servers (the `mcp_remotes` block of the config) have the bots'
exact failure mode for a less forgiving audience. An agent that gets one bad
response deselects the tool and, unlike a person, never retries. A route can
also go missing without anything dying: the noisefloor route was silently
dropped from the reverse proxy once and nobody noticed for weeks.

So the probe speaks the protocol rather than checking the port. `initialize` is
the cheapest read-only JSON-RPC call, and the reply is **parsed**, not scanned:
healthy means a JSON object (plain, or the `data:` frame of an SSE response)
with `"jsonrpc": "2.0"` and either an `error` envelope, which still proves an
MCP server answered, or a `result` carrying `serverInfo`. A substring scan
would pass anything that merely echoed the request back.

The failure verdicts:

| response | verdict |
|---|---|
| any 3xx | `route moved`. Redirects are never followed, since landing somewhere else would prove the wrong URL healthy |
| 401 | healthy: an auth gate proves a live listener **on this path**. It does not prove the path reaches the MCP server, so this is the one deliberately weak result |
| any 4xx with `jsonrpc` in the body | healthy: a real server refusing a bare `initialize` (a 429 from a rate limiter counts, and so does a 403 from a bot rule) |
| any other 4xx/5xx | `route may point at the wrong service` |
| no HTTP answer at all | `unreachable`; if **every** remote is unreachable it collapses into one alarm, because that pattern is this box's egress or the shared proxy, not N separate outages |

Two field notes. `api.liquilens.in` is behind Cloudflare, whose browser
integrity check answers the stock `Python-urllib` signature with 403 error
1010; the probe therefore sends an explicit `User-Agent`. And the probe accepts
`text/event-stream`, which the MCP Python SDK uses by default, so the body read
is bounded by a wall clock and a byte cap rather than by the socket: an SSE
keepalive every 15s would otherwise hold a `read()` open forever, and with
`Type=oneshot` (no default start timeout) that wedges the unit in `activating`,
where the timer never fires again and the whole alarm dies silently. Hence
`TimeoutStartSec=180` in the unit as a second line of defence.

Each probe also sends a best-effort `DELETE` with the `Mcp-Session-Id` the
server handed back, so 288 runs a day do not leave 288 orphaned sessions per
server behind.

## Design rules

- **The alarm never runs through the thing it watches.** Each bot is reported
  via a *different* bot's token (`alert_via` in the config). The script enforces
  that rule rather than trusting the file: a self-referential `alert_via` is
  ignored and the next usable bot is picked.
- **Two consecutive bad runs before alerting**, so one network blip stays quiet.
- **One alert per hour per bot**, plus an explicit `🟢 recovered` message.
- NYX cannot report its own worst failure — laptop asleep, offline or logged
  out. So the Mac *checks in* every 5 min via `fleet-mac-heartbeat.sh` (a
  LaunchAgent). The box requires both a fresh mtime and exactly one `nyx=1`
  line. A fresh `nyx=0` therefore alerts instead of disguising a stopped
  bridge as a healthy host. Missing, unreadable, symlinked, non-regular,
  multiply linked, wrong-owner, or group/world-writable heartbeat files also
  fail loud through the same deduplicated `mac-bots` alarm.

## Configuration (kept out of git)

This repo is public, so none of the operator's chat id, the box address, or the
probe table is in it. The probe table is the sensitive one: unit name plus env
file plus token variable, for every bot, is a ready-made map from any file-read
primitive on the box to every live bot token. Only its *shape* is in git.

Two files on the box:

```bash
# the chat id the alerts go to
echo 'FLEET_OWNER_CHAT=<your telegram user id>' > /etc/fleet-watchdog.env
chmod 600 /etc/fleet-watchdog.env

# what to probe
chmod 600 /etc/fleet-watchdog.json
```

and one on the Mac:

```bash
mkdir -p ~/.config/fleet-watchdog
echo 'root@<box-host>' > ~/.config/fleet-watchdog/box
```

`/etc/fleet-watchdog.json`, worked example (illustrative names, not the live
fleet):

```json
{
  "default_alert_via": "alpha-bot",
  "bots": [
    {
      "unit": "alpha-bot",
      "env": "/etc/alpha-bot.env",
      "var": "ALPHA_BOT_TOKEN",
      "state": "/var/lib/alpha-bot/offset.json",
      "alert_via": "beta-bot"
    },
    {
      "unit": "beta-bot",
      "env": "/etc/beta-bot.env",
      "var": "BETA_BOT_TOKEN",
      "alert_via": "alpha-bot"
    },
    {
      "unit": "gamma-gateway",
      "env": "/home/gamma/.hermes/.env",
      "var": "TELEGRAM_BOT_TOKEN"
    },
    {
      "unit": "delta-bot",
      "env": "/opt/delta/bot/config.json",
      "var": "bot_token"
    }
  ],
  "mcp_remotes": [
    { "name": "mcp-alpha", "url": "https://api.example.com/mcp", "alert_via": "beta-bot" },
    { "name": "mcp-beta",  "url": "https://api.example.com/beta/mcp" }
  ]
}
```

Fields:

| key | meaning |
|---|---|
| `bots[].unit` | systemd unit name, also the label used in alerts and state |
| `bots[].env` | file holding the token. A `.json` suffix is read as JSON, anything else as `KEY=value` lines |
| `bots[].var` | variable (or JSON key) holding the token |
| `bots[].state` | optional; a file whose mtime proves the poll loop turns |
| `bots[].alert_via` | optional; unit whose token sends this bot's alerts. Defaults to `default_alert_via`, and never to the bot itself |
| `mcp_remotes[].name` | label used in alerts and state |
| `mcp_remotes[].url` | the streamable-HTTP MCP endpoint |
| `default_alert_via` | fallback sender for anything without `alert_via`, including the Mac heartbeat |

Point `FLEET_WATCHDOG_CONFIG` elsewhere to test against a scratch file.

Without a chat id the run exits **non-zero** rather than probing mutely, so a
missing `EnvironmentFile` shows up as a `failed` unit in the journal instead of
a green run that could never have paged anyone. A config that parses to no bots
and no remotes exits non-zero for the same reason. A single malformed *entry*
is skipped with a note; it does not take the run down.

## Install

```bash
scp watchdog.py root@box:/opt/fleet-watchdog/watchdog.py
scp fleet-watchdog.{service,timer} root@box:/etc/systemd/system/
# /etc/fleet-watchdog.env and /etc/fleet-watchdog.json must already exist,
# see Configuration above; without them the unit exits non-zero on purpose.
ssh root@box 'systemctl daemon-reload && systemctl enable --now fleet-watchdog.timer'
```

The box runs a **copy** at `/opt/fleet-watchdog/watchdog.py`. Editing this repo
changes nothing in production until that scp runs.

Mac side:

```bash
cp fleet-mac-heartbeat.sh ~/bin/ && chmod +x ~/bin/fleet-mac-heartbeat.sh
cp com.mrinal.fleet-heartbeat.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mrinal.fleet-heartbeat.plist
```

## Companion change

`bot-limits.conf` drop-ins raise `StartLimitIntervalSec` to 300s on each bot
unit so a genuine crash-loop can finally reach `failed`. **Only safe because
this watchdog now pages** — without it, a failed unit is a silent unit.

## Adding a bot

Append an object to `bots` in `/etc/fleet-watchdog.json` with an `alert_via`
pointing at a *different* bot. No code change, no redeploy.

## Testing the alarm

Copy the script, point `FLEET_WATCHDOG_CONFIG` at a scratch config with a bogus
entry, set `CONSECUTIVE = 1` and point `STATE_PATH` at `/tmp`. It should deliver
a real `🔴` message. Do not test by breaking a live bot.
