#!/bin/bash
# Check in with the box so it can tell when the Mac-hosted bot is gone.
# Only @nyx_terminal_bot lives here now (riptide moved to the box 2026-08-02).
# If the Mac is asleep, offline or logged out, nothing touches the heartbeat
# and fleet-watchdog on the box alerts — the one failure the Mac cannot
# report about itself.
set -u
set -o pipefail
nyx_alive=0
if launchctl print \
  "gui/$(id -u)/com.beepboop2025.claude-telegram-bridge" 2>/dev/null |
  awk '
    /^\tstate[[:space:]]*=/ {
      states += 1
      running = ($0 == "\tstate = running")
    }
    END { exit !(states == 1 && running) }
  '
then
  nyx_alive=1
fi
# Host comes from ~/.config/fleet-watchdog/box (one line, user@host) so no
# infrastructure address is committed to a public repo.
BOX=$(cat "$HOME/.config/fleet-watchdog/box" 2>/dev/null) || exit 0
[ -n "$BOX" ] || exit 0
ssh -o ConnectTimeout=15 -o BatchMode=yes -- "$BOX" \
  "set -eu
heartbeat_dir=/var/lib/fleet-watchdog
heartbeat_path=\"\$heartbeat_dir/mac.heartbeat\"
heartbeat_tmp=
umask 077
cleanup() {
  [ -z \"\$heartbeat_tmp\" ] || rm -f -- \"\$heartbeat_tmp\"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
[ ! -L \"\$heartbeat_dir\" ] || exit 1
install -d -o root -g root -m 0700 \"\$heartbeat_dir\"
[ -d \"\$heartbeat_dir\" ] && [ ! -L \"\$heartbeat_dir\" ] || exit 1
heartbeat_tmp=\$(mktemp \"\$heartbeat_dir/.mac.heartbeat.XXXXXX\")
printf '%s\n' 'nyx=${nyx_alive}' > \"\$heartbeat_tmp\"
chown 0:0 \"\$heartbeat_tmp\"
chmod 0644 \"\$heartbeat_tmp\"
mv -fT -- \"\$heartbeat_tmp\" \"\$heartbeat_path\"
heartbeat_tmp=
trap - EXIT HUP INT TERM" \
  >/dev/null 2>&1
