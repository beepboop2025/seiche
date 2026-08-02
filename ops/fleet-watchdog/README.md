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

Both Telegram methods are read-only and neither touches `getUpdates`, so
probing can **never** conflict with a running poller. Verified against all
tokens before rollout.

## Design rules

- **The alarm never runs through the thing it watches.** Each bot is reported
  via a *different* bot's token (see `ALERT_VIA`).
- **Two consecutive bad runs before alerting**, so one network blip stays quiet.
- **One alert per hour per bot**, plus an explicit `🟢 recovered` message.
- The Mac-hosted bots (riptide, nyx) cannot report their own worst failure —
  laptop asleep, offline or logged out. So the Mac *checks in* every 5 min via
  `fleet-mac-heartbeat.sh` (a LaunchAgent), and the box alerts when the
  check-in stops.

## Install

```bash
scp watchdog.py root@box:/opt/fleet-watchdog/watchdog.py
scp fleet-watchdog.{service,timer} root@box:/etc/systemd/system/
ssh root@box 'systemctl daemon-reload && systemctl enable --now fleet-watchdog.timer'
```

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

Append to `BOTS` (unit, env file, token var name, optional state file) and add
an `ALERT_VIA` entry pointing at a *different* bot. Nothing else.

## Testing the alarm

Copy the script, add a bogus entry, set `CONSECUTIVE = 1` and point
`STATE_PATH` at `/tmp`. It should deliver a real `🔴` message. Do not test by
breaking a live bot.
