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

The list of what to probe (units, env files, token variable names, remotes)
is read from CONFIG_PATH, not from this file: only its shape is public. See
the README for the format.

stdlib only, runs as root from a systemd timer.
"""

from __future__ import annotations

import collections
from datetime import date, datetime, timezone
import json
import os
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.request

STATE_PATH = "/var/lib/fleet-watchdog/state.json"
# Never hardcode the operator's chat id: this file lives in a public repo.
# Supplied by /etc/fleet-watchdog.env (EnvironmentFile in the unit).
OWNER_CHAT = os.environ.get("FLEET_OWNER_CHAT", "")
# Which units, env files, token variable names and remotes to probe lives
# beside the chat id, off git, for the same reason. The table is a precise
# map from any file-read primitive on this box to every live bot token, so
# only its shape belongs in a public repo. See README for the format.
CONFIG_PATH = os.environ.get("FLEET_WATCHDOG_CONFIG", "/etc/fleet-watchdog.json")
TIMEOUT = 20
# Alert only after this many consecutive bad runs, so one blip stays quiet.
CONSECUTIVE = 2
# Do not repeat the same alert more often than this.
REALERT_S = 3600
# pending_update_count above this means the poller is not draining its queue.
PENDING_MAX = 25

# Warn two UTC date steps before LiquiLens withholds a rails pack at age 4
# (the product's 3-day SLA rejects only age > 3).  This is a prevention
# threshold, not the publication SLA: age 2 is already a watchdog problem
# even while the endpoint still serves the evidence.
RAILS_WARN_AGE_DAYS = 2
# The public board is intentionally detailed.  Bound it generously so a
# misrouted or unbounded response cannot consume arbitrary memory.
RAILS_READ_MAX = 8 * 1024 * 1024

# Mac-hosted bots report in by touching this file over ssh; if the Mac is
# asleep, offline or logged out, nothing touches it and the box notices.
MAC_HEARTBEAT = "/var/lib/fleet-watchdog/mac.heartbeat"
MAC_STALE_S = 3600
MAC_HEARTBEAT_MAX_BYTES = 64

# What gets probed comes from CONFIG_PATH, never from here. The shape:
#
#   bots         unit name, env file, token variable, optional state file whose
#                mtime proves the poll loop turns, and the unit to alert through
#   mcp_remotes  display name, URL, and the unit to alert through
#   liquilens_rails  public rails URL and the unit to alert through
Bot = collections.namedtuple("Bot", "unit env var state via")
Remote = collections.namedtuple("Remote", "name url via")
RailsProbe = collections.namedtuple("RailsProbe", "url via")
Config = collections.namedtuple("Config", "bots remotes rails default_via")

# The MCP remotes have the bots' exact failure mode (process up, unit active,
# answering nothing) for a less forgiving audience. An agent that gets one bad
# response deselects the tool and, unlike a person, does not retry tomorrow. A
# route can also go missing without anything dying: the noisefloor route was
# silently dropped from the reverse proxy once and nobody noticed. So probe the
# protocol, not the port: a TCP check or a 200 on `/` proves neither that the
# JSON-RPC layer is alive nor that this path still routes to the right server.
#
# initialize is the cheapest read-only call that exercises the JSON-RPC layer.
MCP_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "fleet-watchdog", "version": "1.0"}},
}
# Identify as a real client. api.liquilens.in sits behind Cloudflare, whose
# browser-integrity check answers the stock Python-urllib signature with a 403
# (error 1010), so probing without this reports a healthy server as down.
MCP_UA = "fleet-watchdog/1.0 (+https://seiche.info)"
# MCP probes get a tighter budget than the global TIMEOUT (which the bot checks
# keep): a healthy remote answers initialize in well under a second, and the
# whole run has to fit inside the timer period. Worst case per remote is
# connect + read + teardown, so six dead remotes cost about two minutes.
MCP_TIMEOUT = 8
MCP_DELETE_TIMEOUT = 4
# Wall-clock cap and size cap for reading a probe response body. The per-recv
# socket timeout cannot bound an SSE stream that trickles keepalives forever,
# so the clock, not the socket, has to end the read.
MCP_READ_DEADLINE_S = MCP_TIMEOUT
MCP_READ_MAX = 65536

_CTX = ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect while probing: silently landing somewhere else
    would prove the wrong URL healthy. The 3xx surfaces as an HTTPError and
    check_mcp reports it as a routing problem."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(
    _NoRedirect(), urllib.request.HTTPSHandler(context=_CTX))


def load_config(path: str | None = None) -> Config:
    """Read the probe table. A malformed entry is dropped with a note rather
    than taken as licence to check nothing: a watchdog that silently probes an
    empty list is the failure it exists to catch."""
    path = path or CONFIG_PATH
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception as exc:
        print(f"config {path} unreadable ({type(exc).__name__}), nothing to check")
        raw = {}
    if not isinstance(raw, dict):
        print(f"config {path} is not a JSON object, nothing to check")
        raw = {}

    def _entries(key: str) -> list:
        val = raw.get(key) or []
        if not isinstance(val, list):
            print(f"config {key} is not a list, ignored")
            return []
        return val

    bots: list[Bot] = []
    for i, e in enumerate(_entries("bots")):
        if not (isinstance(e, dict) and e.get("unit") and e.get("env") and e.get("var")):
            print(f"config bots[{i}] needs unit, env and var, skipped")
            continue
        bots.append(Bot(str(e["unit"]), str(e["env"]), str(e["var"]),
                        e.get("state") or None, e.get("alert_via") or ""))

    remotes: list[Remote] = []
    for i, e in enumerate(_entries("mcp_remotes")):
        if not (isinstance(e, dict) and e.get("name") and e.get("url")):
            print(f"config mcp_remotes[{i}] needs name and url, skipped")
            continue
        remotes.append(Remote(str(e["name"]), str(e["url"]), e.get("alert_via") or ""))

    rails = None
    rails_entry = raw.get("liquilens_rails")
    if rails_entry is not None:
        if not (isinstance(rails_entry, dict) and rails_entry.get("url")):
            print("config liquilens_rails needs url, skipped")
        else:
            rails = RailsProbe(str(rails_entry["url"]),
                               str(rails_entry.get("alert_via") or ""))

    default_via = str(raw.get("default_alert_via") or "")
    return Config(bots, remotes, rails, default_via)


def pick_via(name: str, configured: str, cfg: Config) -> str:
    """The alarm must never run through the thing it is reporting on, so that
    rule is enforced here rather than trusted to the config file."""
    for cand in (configured, cfg.default_via):
        if cand and cand != name:
            return cand
    for bot in cfg.bots:
        if bot.unit != name:
            return bot.unit
    return ""


def read_env(path: str, key: str) -> str:
    """Read a secret by name from a KEY=value env file or a JSON config."""
    try:
        if path.endswith(".json"):
            with open(path) as fh:
                return str(json.load(fh).get(key, "") or "")
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except Exception:
        # Any parse trouble (bad JSON, encoding, permissions) means no token,
        # and the caller already treats an empty token as a reportable problem.
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


def check(bot: Bot) -> list[str]:
    """Return a list of human-readable problems; empty means healthy."""
    unit, env, var, state = bot.unit, bot.env, bot.var, bot.state
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


def _parse_rpc(payload: str):
    """Parse a JSON-RPC body that may arrive as plain JSON or as an SSE
    frame. Returns the decoded object, or None if nothing parses."""
    text = payload.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    # SSE: the response rides in the first "data:" line of the stream.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except ValueError:
                return None
    return None


def _read_bounded(resp) -> str:
    """Read a probe response under a wall-clock deadline and a size cap.

    The per-recv socket timeout does not bound an SSE stream that trickles
    keepalives, so a wedged stream could otherwise hold the probe (and the
    whole oneshot run) open indefinitely. Stops on EOF, on the deadline, at
    MCP_READ_MAX bytes, or as soon as a complete JSON-RPC message parses.

    read1 rather than read: read() only returns early at EOF, so on a stream
    the server holds open it would block on a full buffer and then raise on
    the socket timeout, discarding the reply that had already arrived.
    """
    reader = getattr(resp, "read1", None) or resp.read
    deadline = time.monotonic() + MCP_READ_DEADLINE_S
    buf = b""
    while len(buf) < MCP_READ_MAX and time.monotonic() < deadline:
        try:
            chunk = reader(min(8192, MCP_READ_MAX - len(buf)))
        except Exception:
            break  # per-read timeout on an idle stream; keep what arrived
        if not chunk:
            break  # EOF
        buf += chunk
        if _parse_rpc(buf.decode("utf-8", "replace")) is not None:
            break  # got the answer; do not wait out an open SSE stream
    return buf.decode("utf-8", "replace")


def _end_session(url: str, sid) -> None:
    """Best-effort teardown of the session a probe opened, so probes do not
    accumulate server-side sessions. Failure here says nothing about health."""
    if not sid:
        return
    try:
        req = urllib.request.Request(url, method="DELETE", headers={
            "Mcp-Session-Id": sid,
            "User-Agent": MCP_UA,
        })
        with _OPENER.open(req, timeout=MCP_DELETE_TIMEOUT):
            pass
    except Exception:
        pass


def check_mcp(url: str) -> list[str]:
    """Probe one MCP remote over JSON-RPC. Empty list means healthy."""
    body = json.dumps(MCP_INIT).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": MCP_UA,
    })
    try:
        with _OPENER.open(req, timeout=MCP_TIMEOUT) as r:
            sid = r.headers.get("Mcp-Session-Id")
            payload = _read_bounded(r)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            # _NoRedirect keeps the probe from silently following these, so
            # a moved route surfaces instead of proving the wrong URL healthy.
            return [f"route moved (HTTP {e.code}), may point at the wrong service"]
        if e.code == 401:
            # An auth gate answers before the MCP layer does, so a 401 proves
            # a live listener on this path, not that the path routes to the
            # MCP server. Accepted as healthy anyway; the alternative is a
            # permanent false alarm on every authenticated remote.
            return []
        try:
            ebody = e.read(8192).decode("utf-8", "replace")
        except Exception:
            ebody = ""
        # A 4xx is ambiguous: an MCP server may legitimately reject a bare
        # initialize, but a misrouted path answers 4xx too. Distinguish by
        # the body: a real server refuses in JSON-RPC, a wrong route does not.
        # Treating every 4xx as healthy would mask the exact failure this
        # probe exists to catch, so require the protocol marker.
        if 400 <= e.code < 500 and "jsonrpc" in ebody:
            return []
        return [f"HTTP {e.code} on initialize"
                + ("" if ebody else " (no body)")
                + (", route may point at the wrong service"
                   if "jsonrpc" not in ebody else "")]
    except urllib.error.URLError as e:
        # Network-level failure (DNS, refused, TLS, timeout), no HTTP answer
        # at all. main() collapses these when every remote reports the same,
        # because that pattern means box-side egress, not six outages.
        reason = getattr(e, "reason", None)
        if isinstance(reason, str):
            detail = reason
        elif reason is not None:
            detail = type(reason).__name__
        else:
            detail = type(e).__name__
        return [f"unreachable ({detail})"]
    except Exception as e:
        return [f"probe failed ({type(e).__name__})"]

    try:
        return _verdict(url, payload)
    finally:
        # Every path that got this far opened a session, healthy or not, so
        # the teardown belongs here and not on the healthy returns alone.
        _end_session(url, sid)


def _verdict(url: str, payload: str) -> list[str]:
    """Judge one initialize response. Empty list means healthy.

    Health has to be *proved*, so the reply is parsed rather than scanned for
    substrings. A scan for "jsonrpc" passes anything that echoes the request
    back, which is what a mirroring proxy or an error page quoting the request
    does, and looking for '"error"' without '"result"' misreads an error whose
    message happens to use the word.
    """
    obj = _parse_rpc(payload)
    if obj is None:
        preview = " ".join(payload.split())[:80]
        return [f"answered {url.split('/', 3)[-1]} with no parseable JSON-RPC "
                f"message, route may point at the wrong service"
                + (f": {preview}" if preview else " (empty body)")]
    if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
        return ["answered with JSON but not JSON-RPC 2.0, route may point at the wrong service"]
    if "error" in obj:
        # An error envelope still proves an MCP server answered initialize.
        return []
    result = obj.get("result")
    if isinstance(result, dict) and "serverInfo" in result:
        return []
    if "result" not in obj:
        return ["jsonrpc 2.0 reply to initialize carries neither result nor error"]
    return ["initialize result lacks serverInfo, may not be an MCP server"]


def _fetch_json_object(url: str) -> tuple[dict | None, str | None]:
    """GET one bounded JSON object without following redirects.

    This is deliberately separate from the freshness verdict: transport and
    JSON-shape failures are one class of evidence outage, while the caller
    owns the UTC clock and product-specific status contract.
    """
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": MCP_UA,
    })
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as response:
            status_code = getattr(response, "status", None)
            if status_code is None:
                status_code = response.getcode()
            if status_code != 200:
                return None, f"HTTP {status_code} from rails endpoint"
            raw = response.read(RAILS_READ_MAX + 1)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} from rails endpoint"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        detail = type(reason).__name__ if reason is not None else type(exc).__name__
        return None, f"rails endpoint unreachable ({detail})"
    except Exception as exc:
        return None, f"rails endpoint probe failed ({type(exc).__name__})"

    if len(raw) > RAILS_READ_MAX:
        return None, f"rails response exceeds {RAILS_READ_MAX} byte limit"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return None, "rails response is not valid UTF-8 JSON"
    if not isinstance(payload, dict):
        return None, "rails response JSON is not an object"
    return payload, None


def check_liquilens_rails(probe: RailsProbe,
                           current_day: date | None = None) -> list[str]:
    """Fail before the rails pack reaches LiquiLens's public hold.

    ``as_of`` is interpreted against a UTC date supplied by the caller (or the
    current UTC date), never local time and never an age claimed by the remote.
    Missing or contradictory availability fields fail closed: absence of
    evidence must not be reported as healthy.
    """
    payload, transport_problem = _fetch_json_object(probe.url)
    if transport_problem:
        return [transport_problem]

    problems: list[str] = []
    available = payload.get("available")
    if type(available) is not bool:
        problems.append("rails response available is missing or not boolean")
    elif not available:
        problems.append("rails response says available=false")

    stale = payload.get("stale")
    if type(stale) is not bool:
        problems.append("rails response stale is missing or not boolean")
    elif stale:
        problems.append("rails response says stale=true")

    as_of_raw = payload.get("as_of")
    try:
        as_of = date.fromisoformat(as_of_raw)
        if as_of.isoformat() != as_of_raw:
            raise ValueError("as_of is not canonical")
    except (TypeError, ValueError):
        problems.append("rails response as_of is missing or invalid (expected YYYY-MM-DD)")
        return problems

    today = current_day or datetime.now(timezone.utc).date()
    age_days = (today - as_of).days
    if age_days < 0:
        problems.append(f"rails as_of {as_of_raw} is in the future "
                        f"(UTC day {today.isoformat()})")
    elif age_days >= RAILS_WARN_AGE_DAYS:
        problems.append(f"rails evidence age_days={age_days} at UTC day "
                        f"{today.isoformat()} (as_of {as_of_raw}; warn at "
                        f">={RAILS_WARN_AGE_DAYS})")
    return problems


def notify(cfg: Config, via_unit: str, text: str) -> bool:
    for bot in cfg.bots:
        if bot.unit != via_unit:
            continue
        token = read_env(bot.env, bot.var)
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
            data = json.load(fh)
        # A corrupt or wrong-shaped state file must not crash the run; the
        # cost of starting fresh is one extra CONSECUTIVE cycle, nothing more.
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(s: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(s, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def guarded(name: str, fn) -> list[str]:
    """Run one check so that a bug or a bad config entry costs that one check
    and nothing else. Before this, an exception anywhere in the list took main()
    down before save_state(), so no counter advanced and no alert could ever
    fire again."""
    try:
        return fn()
    except Exception as exc:
        return [f"probe for {name} raised {type(exc).__name__}: {exc}"]


def check_mac_heartbeat(now: float | None = None) -> list[str]:
    """Prove that the Mac check-in is fresh *and* says NYX is running.

    The LaunchAgent writes one line through a root SSH command.  Open the file
    without following a final symlink, then inspect and read that same file
    descriptor so a path swap cannot turn the safety check into a different
    read.  This service runs as root, so accepting a file writable by another
    account would let an unrelated local process forge NYX health.
    """
    now = time.time() if now is None else now
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        return ["Mac heartbeat cannot be opened safely (O_NOFOLLOW unavailable)"]
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if not nonblock:
        return ["Mac heartbeat cannot be opened safely (O_NONBLOCK unavailable)"]

    # O_NONBLOCK matters before fstat: opening a FIFO read-only would otherwise
    # wait forever for a writer, preventing this fail-loud check from running.
    flags = os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(MAC_HEARTBEAT, flags)
    except FileNotFoundError:
        return [f"Mac heartbeat missing: {MAC_HEARTBEAT}"]
    except OSError as exc:
        return [f"Mac heartbeat unreadable or unsafe ({type(exc).__name__})"]

    try:
        info = os.fstat(fd)
        unsafe = []
        if not stat.S_ISREG(info.st_mode):
            unsafe.append("not a regular file")
        if info.st_uid != os.geteuid():
            unsafe.append("not owned by the watchdog account")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            unsafe.append("writable by another account")
        if info.st_nlink != 1:
            unsafe.append("has multiple hard links")
        if unsafe:
            return [f"Mac heartbeat unsafe ({'; '.join(unsafe)})"]

        chunks = []
        remaining = MAC_HEARTBEAT_MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    except OSError as exc:
        return [f"Mac heartbeat unreadable ({type(exc).__name__})"]
    finally:
        os.close(fd)

    # A terminal newline is normal for a line-oriented file; everything else
    # (including CRLF, whitespace and a second line) is deliberately rejected.
    if payload == b"nyx=0" or payload == b"nyx=0\n":
        return ["Mac heartbeat reports nyx=0 (NYX bridge is not active)"]
    if payload not in (b"nyx=1", b"nyx=1\n"):
        return ["Mac heartbeat malformed (expected exactly one line: nyx=1)"]

    age = now - info.st_mtime
    if age > MAC_STALE_S:
        return [f"no Mac check-in for {int(age // 60)} min "
                f"(NYX bridge unreachable: Mac asleep, offline or logged out)"]
    return []


def main() -> int:
    if not OWNER_CHAT:
        # Refuse to run mute: a failed unit is greppable in the journal, a
        # watchdog that probes but can alert nobody is invisible.
        print("FLEET_OWNER_CHAT unset, alerts cannot be delivered")
        return 1
    cfg = load_config()
    if not cfg.bots and not cfg.remotes and cfg.rails is None:
        print(f"no bots, MCP remotes or rails probe configured in {CONFIG_PATH}")
        return 1

    state = load_state()
    now = time.time()
    # name, unit to alert through, problems
    checks = [(b.unit, pick_via(b.unit, b.via, cfg), guarded(b.unit, lambda b=b: check(b)))
              for b in cfg.bots]

    # Keep the established state key: payload failures and stale check-ins are
    # one NYX alarm, sharing the same debounce/re-alert history.
    checks.append(("mac-bots", pick_via("mac-bots", "", cfg),
                   guarded("mac-bots", lambda: check_mac_heartbeat(now))))

    if cfg.rails is not None:
        rails_day = datetime.fromtimestamp(now, timezone.utc).date()
        checks.append((
            "liquilens-rails",
            pick_via("liquilens-rails", cfg.rails.via, cfg),
            guarded("liquilens-rails", lambda: check_liquilens_rails(
                cfg.rails, rails_day)),
        ))

    mcp_checks = [(r.name, pick_via(r.name, r.via, cfg),
                   guarded(r.name, lambda r=r: check_mcp(r.url)))
                  for r in cfg.remotes]
    # Every remote failing at the network level is one fault, this box's
    # egress, not N outages, and they also share one reverse proxy. Collapse
    # it into a single alarm; per-remote HTTP failures still report per-remote.
    collapsed = bool(mcp_checks) and all(
        any(p.startswith("unreachable") for p in probs)
        for _, _, probs in mcp_checks)
    if collapsed:
        checks.append(("mcp", pick_via("mcp", "", cfg),
                       [f"all {len(mcp_checks)} remotes unreachable, "
                        f"likely box-side egress"]))
    else:
        # The synthetic name is reported healthy rather than omitted. Left out
        # of checks it is never visited, so its fail counter stays where the
        # collapse left it: the recovery message never arrives, and the next
        # collapse alerts on its first run instead of waiting for CONSECUTIVE.
        # An alarm that cannot say it is over is an alarm nobody trusts twice.
        checks.append(("mcp", pick_via("mcp", "", cfg), []))
        checks.extend(mcp_checks)

    for name, via, problems in checks:
        st = state.get(name)
        if not isinstance(st, dict):
            st = {}
        st["fails"] = int(st.get("fails") or 0)
        st["alerted_at"] = float(st.get("alerted_at") or 0)
        state[name] = st
        if problems:
            st["fails"] += 1
            st["last"] = "; ".join(problems)
            due = (st["fails"] >= CONSECUTIVE
                   and now - st["alerted_at"] > REALERT_S)
            if due and notify(cfg, via, f"🔴 {name}: {st['last']}"):
                st["alerted_at"] = now
        else:
            if st["fails"] >= CONSECUTIVE and st["alerted_at"]:
                notify(cfg, via, f"🟢 {name}: recovered")
            st["fails"] = 0
            st["alerted_at"] = 0
            st.pop("last", None)

    state["_last_run"] = now
    save_state(state)
    bad = [n for n, _, p in checks if p]
    print(f"checked {len(checks)}; unhealthy: {bad if bad else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
