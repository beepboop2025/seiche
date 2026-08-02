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
# Never hardcode the operator's chat id: this file lives in a public repo.
# Supplied by /etc/fleet-watchdog.env (EnvironmentFile in the unit).
OWNER_CHAT = os.environ.get("FLEET_OWNER_CHAT", "")
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
    ("hermes-gateway", "/home/hermes/.hermes/.env", "TELEGRAM_BOT_TOKEN", None),
    ("riptide-bot",    "/opt/riptide/bot/config.json", "bot_token",        None),
]
# Report a bot through a bot that is not itself.
ALERT_VIA = {
    "seiche-bot": "undertow-bot",
    "liquilens-bot": "seiche-bot",
    "undertow-bot": "seiche-bot",
    "scamshield-bot": "seiche-bot",
    "anake-gateway": "seiche-bot",
    "hermes-gateway": "seiche-bot",
    "riptide-bot": "seiche-bot",
    "_mac": "seiche-bot",
    # Report a remote through a bot that does not share its service, so a dead
    # seiche stack cannot swallow its own alarm.
    "mcp-seiche": "undertow-bot",
    "mcp-palimpsest": "undertow-bot",
    "mcp-undertow": "seiche-bot",
    "mcp-liquilens": "seiche-bot",
    "mcp-breach": "seiche-bot",
    "mcp-groundcheck": "undertow-bot",
}
# Mac-hosted bots report in by touching this file over ssh; if the Mac is
# asleep, offline or logged out, nothing touches it and the box notices.
MAC_HEARTBEAT = "/var/lib/fleet-watchdog/mac.heartbeat"
MAC_STALE_S = 3600

# The MCP remotes have the bots' exact failure mode — process up, unit active,
# answering nothing — for a less forgiving audience. An agent that gets one bad
# response deselects the tool and, unlike a person, does not retry tomorrow. A
# route can also go missing without anything dying: the noisefloor route was
# silently dropped from the reverse proxy once and nobody noticed. So probe the
# protocol, not the port: a TCP check or a 200 on `/` proves neither that the
# JSON-RPC layer is alive nor that this path still routes to the right server.
MCP_REMOTES = [
    ("mcp-seiche",      "https://api.seiche.info/mcp"),
    ("mcp-liquilens",   "https://api.liquilens.in/mcp"),
    ("mcp-palimpsest",  "https://api.seiche.info/palimpsest/mcp"),
    ("mcp-undertow",    "https://api.seiche.info/undertow/mcp"),
    ("mcp-breach",      "https://breach.seiche.info/mcp"),
    ("mcp-groundcheck", "https://groundcheck.seiche.info/mcp"),
]
# initialize is the cheapest read-only call that exercises the JSON-RPC layer.
MCP_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "fleet-watchdog", "version": "1.0"}},
}
# Identify as a real client. api.liquilens.in sits behind Cloudflare, whose
# browser-integrity check answers the stock Python-urllib signature with a 403
# (error 1010) — probing without this would report a healthy server as down.
MCP_UA = "fleet-watchdog/1.0 (+https://seiche.info)"

_CTX = ssl.create_default_context()


def read_env(path: str, key: str) -> str:
    """Read a secret by name from a KEY=value env file or a JSON config."""
    try:
        if path.endswith(".json"):
            import json
            with open(path) as fh:
                return str(json.load(fh).get(key, "") or "")
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except (OSError, ValueError):
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


def check_mcp(url: str) -> list[str]:
    """Probe one MCP remote over JSON-RPC. Empty list means healthy."""
    body = json.dumps(MCP_INIT).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": MCP_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
            payload = r.read(65536).decode("utf-8", "replace")
        if "jsonrpc" not in payload:
            return [f"answered {url.split('/', 3)[-1]} but not as JSON-RPC "
                    f"— route may point at the wrong service"]
        if '"error"' in payload and '"result"' not in payload:
            return [f"JSON-RPC error on initialize: {payload[:120]}"]
        return []
    except urllib.error.HTTPError as e:
        # A 4xx is ambiguous: an MCP server may legitimately reject a bare
        # initialize, but a misrouted path answers 4xx too. Distinguish by the
        # body — a real server refuses in JSON-RPC, a wrong route does not.
        # Treating every 4xx as healthy would mask the exact failure this
        # probe exists to catch, so require the protocol marker.
        if e.code == 401:
            return []  # auth gate: server is up and this path routes correctly
        try:
            body = e.read(8192).decode("utf-8", "replace")
        except Exception:
            body = ""
        if e.code in (400, 405, 406) and "jsonrpc" in body:
            return []
        return [f"HTTP {e.code} on initialize"
                + ("" if body else " (no body)")
                + (" — route may point at the wrong service"
                   if "jsonrpc" not in body else "")]
    except Exception as e:  # network, DNS, TLS
        # Matches the bot path: transient box-side trouble is not a fault of
        # the thing being watched. CONSECUTIVE still catches a real outage.
        return [f"unreachable ({type(e).__name__})"]


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
                                    f"(nyx bridge unreachable: Mac asleep, offline or logged out)"]
                       if age > MAC_STALE_S else []))
    except OSError:
        pass  # heartbeat not established yet; stay quiet rather than cry wolf

    checks.extend((name, check_mcp(url)) for name, url in MCP_REMOTES)

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
