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
| LiquiLens `GET /api/public-signals/rails` | the rails pack is approaching its public hold, unavailable, stale, future-dated, malformed, or unreachable |
| LiquiLens runner-maintenance receipt, marker and systemd units | a deferred runner restart, overdue maintenance debt, failed/stale checker, disabled timer, or stopped runner |
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
- The LiquiLens rails probe is preventive: it uses the UTC date encoded by
  `as_of` and alerts at `age_days >= 2`, before the API withholds a pack at
  age 4 (`age_days > 3`). It also fails closed on a non-200 response, non-object JSON,
  missing/non-canonical/future `as_of`, non-boolean status fields,
  `available=false`, or `stale=true`. The probe is a read-only GET and its
  alert goes only to the configured owner chat; it never posts to a public
  channel.
- The runner-maintenance probe does not execute `needrestart`. A separate
  LiquiLens-owned 15-minute checker produces a root-private status receipt and
  retains an active debt marker until a clean scan plus a proven runner restart
  resolves it. Its 23-hour-44-minute deadline reserves one full 15-minute timer
  interval plus `AccuracySec=1m` inside the nominal 24-hour
  package-to-enforcement SLO. The watchdog independently requires an enabled/waiting timer, a
  successful expected-inactive oneshot, a running repository runner, and a
  fresh receipt bound to the current boot and runner generation. Missing,
  malformed, future-dated, stale, unsafe, or contradictory evidence fails
  closed through the ordinary private debounce/recovery path.
- NYX cannot report its own worst failure — laptop asleep, offline or logged
  out. So the Mac *checks in* every 5 min via `fleet-mac-heartbeat.sh` (a
  LaunchAgent). The box requires both a fresh mtime and exactly one `nyx=1`
  line. A fresh `nyx=0` therefore alerts instead of disguising a stopped
  bridge as a healthy host. The producer emits `nyx=1` only when a successful
  `launchctl print` reports the bridge's exact state as `running`; a loaded but
  waiting/exited job and a missing job both emit `nyx=0`. Missing, unreadable,
  symlinked, non-regular, multiply linked, wrong-owner, or
  group/world-writable heartbeat files also fail loud through the same
  deduplicated `mac-bots` alarm.
- The SSH writer connects as root, keeps its destination directory root-only,
  creates the heartbeat there as a fresh regular file, sets owner `0:0` and
  mode `0644`, then
  atomically renames it over the public path. Readers therefore see either the
  complete old heartbeat or the complete new one; an old symlink or hard-linked
  destination is replaced rather than followed or modified in place.

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

The `root@` account is part of the heartbeat contract: the producer refuses to
publish a replacement if it cannot enforce the root ownership and safe mode
required by the receiver.

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
  ],
  "liquilens_rails": {
    "url": "https://api.liquilens.in/api/public-signals/rails",
    "alert_via": "beta-bot"
  },
  "maintenance_status": {
    "name": "liquilens-runner-restart-debt",
    "status_file": "/var/lib/liquilens-runner-maintenance/status.json",
    "debt_file": "/var/lib/liquilens-runner-maintenance/restart-debt.json",
    "service_unit": "liquilens-runner-restart-debt.service",
    "timer_unit": "liquilens-runner-restart-debt.timer",
    "monitored_unit": "actions.runner.beepboop2025-LiquiLens.hetzner-cpx32.service",
    "max_age_seconds": 1200
  }
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
| `liquilens_rails.url` | the public LiquiLens rails JSON endpoint; use `https://api.liquilens.in/api/public-signals/rails` |
| `liquilens_rails.alert_via` | optional bot unit that sends the private owner alert; the state/alert label is fixed as `liquilens-rails` |
| `maintenance_status.name` | bounded state/alert label; the installed value is `liquilens-runner-restart-debt` |
| `maintenance_status.status_file` | root-owned mode-0600 v1 status receipt in a root-owned mode-0700 directory |
| `maintenance_status.debt_file` | optional active v1 debt marker beside the status receipt; its presence is always unhealthy |
| `maintenance_status.service_unit` | timer-triggered checker oneshot; healthy steady state is `inactive/dead`, `Result=success`, exit 0 |
| `maintenance_status.timer_unit` | checker timer, which must be `enabled` and `active/waiting` |
| `maintenance_status.monitored_unit` | exact runner unit whose active/running state and generation bind the receipt |
| `maintenance_status.max_age_seconds` | bounded receipt age; 1200 seconds leaves four minutes beyond the checker's 15-minute interval plus one-minute accuracy window, while detecting a missed run sooner than a two-interval threshold |
| `maintenance_status.alert_via` | optional private sender override; omitted by the installer so the existing default route is preserved |
| `default_alert_via` | fallback sender for anything without `alert_via`, including the Mac heartbeat |

Point `FLEET_WATCHDOG_CONFIG` elsewhere to test against a scratch file.

`liquilens_rails` is a safe, non-secret config key: its values are a public URL
and an existing sender unit name. Do not place credentials or signed query
parameters in the URL.

The maintenance receipts contain only schema identifiers, the exact runner
unit, a bounded reason, UTC/epoch clocks, a derived debt age, boot and systemd
generation IDs, and a source SHA. A clean receipt carries a null debt age; a
debt receipt, or an error receipt that could load the immutable marker, carries
the exact nonnegative `checked_at_epoch - first_seen_epoch`. They must never
contain command output, environment values, tokens, URLs or process arguments.
The watchdog opens them relative to a bound directory descriptor with
`O_NOFOLLOW`, enforces owner/mode/type/link and size limits, and never includes
their payload or reason text in an alert.

Without a chat id the run exits **non-zero** rather than probing mutely, so a
missing `EnvironmentFile` shows up as a `failed` unit in the journal instead of
a green run that could never have paged anyone. A config that parses to no bots,
remotes, rails probe or maintenance probe exits non-zero for the same reason.
A malformed bot/remote entry is skipped with a note; a malformed opted-in
`maintenance_status` becomes a synthetic `watchdog-config` failure so the new
coverage cannot silently disappear.

## Install

Install only from a clean canonical Seiche Git worktree checked out at the
reviewed exact SHA, after the LiquiLens maintenance checker has completed its
first valid scan:

```bash
sudo ops/fleet-watchdog/install.sh --check \
  --source-sha <exact-40-hex-Seiche-commit>
sudo ops/fleet-watchdog/install.sh --install \
  --source-sha <exact-40-hex-Seiche-commit>
```

The installer refuses a noncanonical origin, dirty worktree, or `HEAD` that is
not the supplied SHA. It materializes `watchdog.py` from the commit object into
a root-private candidate and installs only those pinned bytes, rather than
trusting a mutable adjacent file. It never ships or reconstructs the private
config. It locks the transaction, reads the exact current root-owned
`/etc/fleet-watchdog.json`, and
either adds the one exact `maintenance_status` object or proves it is already
identical. It deep-compares every pre-existing key/value, records the config
preimage hash, stops the timer, waits for the oneshot reader to finish, then
checks the preimage again before atomic replacement. A differing existing
maintenance object or any concurrent script/config change aborts before
mutation.

The current script and config are captured inside a new root-only staging
release. Any failed install restores those exact bytes and the timer's prior
enabled/active state. A successful install runs one real oneshot and atomically
promotes a prepared `fleet-watchdog-release.v2` receipt under
`/var/lib/fleet-watchdog/releases/<source-sha>/`. Only after the timer's exact
prior state is restored does it add `release-commit.json`, which binds the
receipt hash and source SHA. A directory without that commit marker is failed
or incomplete recovery evidence, never a canonical successful release. The
receipt contains hashes, modes, source identity, timer state and
verification—not configuration values. Never restore an older release's
private config over the current fleet map.

The box runs a **copy** at `/opt/fleet-watchdog/watchdog.py`. Editing this repo
changes nothing in production until the guarded installer runs.

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
