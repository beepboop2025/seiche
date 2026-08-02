#!/bin/bash
# Check in with the box so it can tell when the Mac-hosted bots are gone.
# If the Mac is asleep, offline or logged out, nothing touches the heartbeat
# and fleet-watchdog on the box alerts — the one failure the Mac cannot
# report about itself.
set -u
riptide_alive=0; nyx_alive=0
launchctl print "gui/$(id -u)/com.mrinal.riptide-bot" >/dev/null 2>&1 && riptide_alive=1
launchctl print "gui/$(id -u)/com.mrinal.claude-telegram" >/dev/null 2>&1 && nyx_alive=1
msg="riptide=${riptide_alive} nyx=${nyx_alive}"
ssh -o ConnectTimeout=15 -o BatchMode=yes root@167.233.225.54 \
  "mkdir -p /var/lib/fleet-watchdog && printf '%s\n' '${msg}' > /var/lib/fleet-watchdog/mac.heartbeat" \
  >/dev/null 2>&1
