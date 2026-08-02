#!/bin/bash
# Check in with the box so it can tell when the Mac-hosted bot is gone.
# Only @nyx_terminal_bot lives here now (riptide moved to the box 2026-08-02).
# If the Mac is asleep, offline or logged out, nothing touches the heartbeat
# and fleet-watchdog on the box alerts — the one failure the Mac cannot
# report about itself.
set -u
nyx_alive=0
launchctl print "gui/$(id -u)/com.mrinal.claude-telegram" >/dev/null 2>&1 && nyx_alive=1
# Host comes from ~/.config/fleet-watchdog/box (one line, user@host) so no
# infrastructure address is committed to a public repo.
BOX=$(cat "$HOME/.config/fleet-watchdog/box" 2>/dev/null) || exit 0
[ -n "$BOX" ] || exit 0
ssh -o ConnectTimeout=15 -o BatchMode=yes "$BOX" \
  "mkdir -p /var/lib/fleet-watchdog && printf '%s\n' 'nyx=${nyx_alive}' > /var/lib/fleet-watchdog/mac.heartbeat" \
  >/dev/null 2>&1
