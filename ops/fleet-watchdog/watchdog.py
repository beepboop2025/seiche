#!/usr/bin/env python3
"""Fleet Telegram-bot watchdog.

Why this exists: every bot in this fleet has a failure mode where the process
stays alive and systemd still reports `active` — a revoked token, a 409 from a
second poller, a wedged handler. Two bots were silently dead for weeks that
way. systemd cannot catch it (StartLimitIntervalSec == RestartSec makes the
restart burst unreachable, so no unit ever reaches `failed`), and the bots
block-buffer stdout so there is nothing in the journal to scrape either.

So this probes from OUTSIDE, on a timer, using only read-only Telegram methods:

  getMe             -> token revoked / invalid          (the observed failure)
  getWebhookInfo    -> pending_update_count climbing    (alive but not consuming)
                    -> a webhook someone set steals updates from the poller
  state file mtime  -> the poll loop stopped turning
  systemctl         -> the unit died outright

Neither method touches getUpdates, so probing can never conflict with a running
poller. Alerts are sent through a DIFFERENT bot than the one being reported —
an alarm must not depend on the thing it is watching.

stdlib only, runs as root from a systemd timer.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request

STATE_PATH = "/var/lib/fleet-watchdog/state.json"
OWNER_CHAT = "8727818928"
TIMEOUT = 20
# Alert only after this many consecutive bad runs, so one blip stays quiet.
CONSECUTIVE = 2
# Do not repeat the same alert more often than this.
REALERT_S = 3600
# pending_update_count above this means the poller is not draining its queue.
PENDING_MAX = 25

# unit, env file, token var, optional state file whose mtime proves the loop turns
BOTS = [
    ("seiche-bot",     "/etc/seiche-bot.env",     "SEICHE_BOT_TOKEN",     "/var/lib/seiche-bot/offset.json"),
    ("liquilens-bot",  "/etc/liquilens-bot.env",  "LIQUILENS_BOT_TOKEN",  "/var/lib/liquilens-bot/offset.json"),
    ("undertow-bot",   "/etc/undertow-bot.env",   "UNDERTOW_BOT_TOKEN",   None),
    ("scamshield-bot", "/etc/scamshield.env",     "SCAMSHIELD_TOKEN",     None),
    ("anake-gateway",  "/home/anake/.hermes/.env", "TELEGRAM_BOT_TOKEN",  None),
]
# Report a bot through a bot that is not itself.
ALERT_VIA = {
    "seiche-bot": "undertow-bot",
    "liquilens-bot": "seiche-bot",
    "undertow-bot": "seiche-bot",
    "scamshield-bot": "seiche-bot",
    "anake-gateway": "seiche-bot",
    "_mac": "seiche-bot",
}
# Mac-hosted bots report in by touching this file over ssh; if the Mac is
# asleep, offline or logged out, nothing touches it and the box notices.
MAC_HEARTBEAT = "/var/lib/fleet-watchdog/mac.heartbeat"
MAC_STALE_S = 3600

_CTX = ssl.create_default_context()


def read_env(path: str, key: str) -> str:
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def api(token: str, method: str) -> dict:
    """Read-only Bot API call. Never logs the URL (it embeds the token)."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT, context=_CTX) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "error_code": e.code, "description": "http error"}
    except Exception as e:  # network, DNS, TLS
        return {"ok": False, "error_code": 0, "description": type(e).__name__}


def unit_active(unit: str) -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", unit],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() == "active"
    except Exception:
        return False


def check(unit: str, env: str, var: str, state: str | None) -> list[str]:
    """Return a list of human-readable problems; empty means healthy."""
    problems: list[str] = []
    if not unit_active(unit):
        problems.append(f"unit {unit} is not active")

    token = read_env(env, var)
    if not token:
        problems.append(f"no token in {env} ({var})")
        return problems

    me = api(token, "getMe")
    if not me.get("ok"):
        code = me.get("error_code")
        if code == 0:
            # transient network trouble on the box, not a bot fault
            return problems
        problems.append(f"getMe failed ({code}: {me.get('description')})"
                        + (" — token revoked" if code == 401 else ""))
        return problems

    hook = api(token, "getWebhookInfo")
    if hook.get("ok"):
        res = hook.get("result", {})
        if res.get("url"):
            problems.append(f"a webhook is set ({res['url'][:40]}) — it steals updates from the poller")
        pending = res.get("pending_update_count", 0)
        if pending > PENDING_MAX:
            problems.append(f"{pending} updates pending — not consuming")
        err = res.get("last_error_message")
        if err:
            problems.append(f"telegram reports: {err[:70]}")

    if state:
        try:
            age = time.time() - os.path.getmtime(state)
            if age > 300:
                problems.append(f"poll loop stalled — {os.path.basename(state)} is {int(age)}s old")
        except OSError:
            problems.append(f"state file missing: {state}")
    return problems


def notify(via_unit: str, text: str) -> bool:
    for unit, env, var, _ in BOTS:
        if unit != via_unit:
            continue
        token = read_env(env, var)
        if not token:
            break
        data = json.dumps({"chat_id": OWNER_CHAT, "text": text,
                           "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
                return json.load(r).get("ok", False)
        except Exception:
            return False
    return False


def load_state() -> dict:
    try:
        with open(STATE_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(s: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(s, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def main() -> int:
    state = load_state()
    now = time.time()
    checks = [(u, check(u, e, v, s)) for u, e, v, s in BOTS]

    # the Mac-hosted bots (riptide, nyx) check in by touching a file over ssh
    try:
        age = now - os.path.getmtime(MAC_HEARTBEAT)
        checks.append(("mac-bots", [f"no Mac check-in for {int(age // 60)} min "
                                    f"(riptide + nyx unreachable: asleep, offline or logged out)"]
                       if age > MAC_STALE_S else []))
    except OSError:
        pass  # heartbeat not established yet; stay quiet rather than cry wolf

    for name, problems in checks:
        st = state.setdefault(name, {"fails": 0, "alerted_at": 0})
        if problems:
            st["fails"] += 1
            st["last"] = "; ".join(problems)
            due = (st["fails"] >= CONSECUTIVE
                   and now - st.get("alerted_at", 0) > REALERT_S)
            if due:
                via = ALERT_VIA.get("_mac" if name == "mac-bots" else name, "seiche-bot")
                if notify(via, f"🔴 {name}: {st['last']}"):
                    st["alerted_at"] = now
        else:
            if st["fails"] >= CONSECUTIVE and st.get("alerted_at"):
                via = ALERT_VIA.get("_mac" if name == "mac-bots" else name, "seiche-bot")
                notify(via, f"🟢 {name}: recovered")
            st["fails"] = 0
            st["alerted_at"] = 0
            st.pop("last", None)

    state["_last_run"] = now
    save_state(state)
    bad = [n for n, p in checks if p]
    print(f"checked {len(checks)}; unhealthy: {bad if bad else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
