#!/usr/bin/env python3
"""Decision-grade 24-hour usage digest for the Seiche product fleet.

Edge requests describe reachability and scanner pressure. Product activation is
counted separately from privacy-safe post-dispatch journal events. No request
arguments, caller identifiers, tokens, or User-Agent strings enter those events.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LOG = Path("/var/log/caddy/access.log")
FUNNEL = Path("/var/lib/groundcheck/funnel.jsonl")
ARMED_STATE = Path("/var/lib/fleet-usage/activation-armed.json")
OWNER_CHAT = "8727818928"
TOKEN_ENV = (Path("/etc/seiche-bot.env"), "SEICHE_BOT_TOKEN")
WINDOW_S = 24 * 3600

# (host, URI prefix, product bucket). First match wins; order matters.
BUCKETS = [
    ("api.seiche.info", "/undertow/mcp", "undertow-mcp"),
    ("api.seiche.info", "/undertow/x402", "undertow-x402"),
    ("api.seiche.info", "/undertow/", "undertow-packs"),
    ("api.seiche.info", "/palimpsest/mcp", "palimpsest-mcp"),
    ("api.seiche.info", "/noisefloor/", "noisefloor-mcp"),
    ("api.seiche.info", "/mcp", "seiche-mcp"),
    ("api.seiche.info", "/api/", "seiche-api"),
    ("api.seiche.info", "/anake-nyx/", "anake-feeds"),
    ("groundcheck.seiche.info", "/mcp", "groundcheck-mcp"),
    ("groundcheck.seiche.info", "/", "groundcheck-api"),
    ("breach.seiche.info", "/", "breach-mcp"),
]

ACTIVATION_PRODUCTS = (
    "seiche", "undertow", "palimpsest", "groundcheck", "breach", "noisefloor",
)
ACTIVATION_UNITS = (
    "seiche-api.service",
    "undertow-mcp.service",
    "palimpsest-mcp.service",
    "groundcheck-engine.service",
    "breach-mcp.service",
    "noisefloor-mcp.service",
)
ACTIVATION_RE = re.compile(
    r"\bmcp_activation product=(?P<product>[a-z0-9-]+) "
    r"surface=(?P<surface>public|subscriber|paid) "
    r"tool=(?P<tool>[A-Za-z0-9_.:-]+) "
    r"outcome=(?P<outcome>success|error)"
    r"(?: origin=(?P<origin>edge|direct|unknown))?\b"
)
LEGACY_UNDERTOW_RE = re.compile(
    r"\bundertow mcp: activation "
    r"surface=(?P<surface>public|subscriber) "
    r"tool=(?P<tool>[A-Za-z0-9_.:-]+) "
    r"outcome=(?P<outcome>success|error)"
)

# Explicit automation only. Generic runtimes (node, undici, python-httpx,
# go-http-client, blank UAs) remain unclassified because real clients use them.
AUTOMATION_MARKERS = (
    ("fleet-watchdog", "owned-monitor"),
    ("sentineloracle", "external-monitor"),
    ("mcpbeat", "external-monitor"),
    ("aisec-registry", "directory-probe"),
    ("agenstrybot", "directory-probe"),
    ("agent-tools.cloud-crawler", "directory-probe"),
    ("agentseo", "directory-probe"),
    ("agenttrust-monitor", "external-monitor"),
    ("reliability-bureau-spike", "external-monitor"),
    ("mcpwatch", "external-monitor"),
    ("io.verifymcp/probe", "directory-probe"),
    ("agentindexbot", "directory-probe"),
    ("mcpwitness", "directory-probe"),
    ("api-forge-mcp-index", "directory-probe"),
    ("aive-mcp-endpointprobe", "directory-probe"),
    ("agentalmanac-snapshot", "directory-probe"),
    ("verifymcp-ownersbot", "directory-probe"),
    ("catalog-health", "external-monitor"),
    ("mcphq-probe", "directory-probe"),
    ("mj12bot", "crawler"),
    ("carbonmonitor", "external-monitor"),
    ("x402-list-monitor", "external-monitor"),
    ("mcpregistry-bot", "directory-probe"),
    ("402explorer", "directory-probe"),
    ("l9scan", "scanner"),
    ("leakix", "scanner"),
    ("mpp32-health", "external-monitor"),
    ("uptime", "external-monitor"),
    ("statuscake", "external-monitor"),
    ("pingdom", "external-monitor"),
    ("censys", "scanner"),
    ("shodan", "scanner"),
    ("nmap", "scanner"),
    ("masscan", "scanner"),
    ("expanse", "scanner"),
    ("internetmeasurement", "scanner"),
)


def bucket_for(host: str, uri: str) -> str | None:
    for expected_host, prefix, name in BUCKETS:
        if host == expected_host and uri.startswith(prefix):
            return name
    return None


def automation_category(user_agent: str) -> str | None:
    folded = (user_agent or "").casefold()
    for marker, category in AUTOMATION_MARKERS:
        if marker in folded:
            return category
    return None


def _edge_stat() -> dict:
    return {
        "req": 0,
        "post": 0,
        "err": 0,
        "ips": set(),
        "automation": Counter(),
        "automation_req": 0,
        "automation_post": 0,
    }


def _access_log_paths(path: Path, rotated_paths: Iterable[Path] | None) -> list[Path]:
    if rotated_paths is not None:
        return [*rotated_paths, path]
    if path != LOG:
        return [path]
    return [*sorted(path.parent.glob("access-*.log.gz")), path]


def parse_log(cutoff: float, path: Path = LOG,
              rotated_paths: Iterable[Path] | None = None) -> dict:
    stats = defaultdict(_edge_stat)
    seen = set()
    for log_path in _access_log_paths(path, rotated_paths):
        opener = gzip.open if log_path.suffix == ".gz" else open
        try:
            fh = opener(log_path, mode="rt", encoding="utf-8")
        except OSError:
            continue
        try:
            lines = fh
            for line in lines:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if entry.get("ts", 0) < cutoff:
                    continue
                request = entry.get("request") or {}
                name = bucket_for(request.get("host", ""), request.get("uri", ""))
                if not name:
                    continue
                # Caddy normally renames without overlap. This identity guard
                # also makes a copied rotation boundary harmless.
                identity = (
                    entry.get("ts"), request.get("remote_ip"),
                    request.get("remote_port"), request.get("method"),
                    request.get("host"), request.get("uri"), entry.get("status"),
                    entry.get("duration"), entry.get("size"),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                stat = stats[name]
                stat["req"] += 1
                is_post = request.get("method") == "POST"
                stat["post"] += int(is_post)
                stat["err"] += int(int(entry.get("status", 0)) >= 400)
                ip = request.get("remote_ip") or request.get("client_ip") or ""
                if ip:
                    stat["ips"].add(ip)
                raw_ua = (request.get("headers") or {}).get("User-Agent") or ""
                ua = raw_ua[0] if isinstance(raw_ua, list) and raw_ua else raw_ua
                category = automation_category(ua if isinstance(ua, str) else "")
                if category:
                    stat["automation"][category] += 1
                    stat["automation_req"] += 1
                    stat["automation_post"] += int(is_post)
        except OSError:
            # A corrupt/partially-written rotation must not erase the current
            # log's evidence; the digest remains best-effort and says what read.
            continue
        finally:
            fh.close()
    return stats


def funnel_counts(cutoff: float, path: Path = FUNNEL) -> Counter | None:
    counts = Counter()
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            stamp = entry.get("ts")
            if isinstance(stamp, str):
                try:
                    stamp = datetime.fromisoformat(
                        stamp.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
            if isinstance(stamp, (int, float)) and stamp >= cutoff:
                counts[str(entry.get("stage", "?"))] += 1
    return counts


def _activation_stat() -> dict:
    return {
        "known": 0,
        "success": 0,
        "error": 0,
        "invalid": 0,
        "tools": Counter(),
        "surfaces": Counter(),
        "origins": Counter(),
    }


def parse_activation_lines(lines: Iterable[str]) -> dict:
    """Parse only the fixed event grammar; arbitrary journal prose is ignored."""
    stats = defaultdict(_activation_stat)
    for line in lines:
        match = ACTIVATION_RE.search(line)
        if match:
            event = match.groupdict()
            event["origin"] = event.get("origin") or "unknown"
        else:
            legacy = LEGACY_UNDERTOW_RE.search(line)
            if not legacy:
                continue
            event = {"product": "undertow", "origin": "unknown",
                     **legacy.groupdict()}
        stat = stats[event["product"]]
        if event["tool"] == "unknown":
            stat["invalid"] += 1
            continue
        stat["known"] += 1
        stat[event["outcome"]] += 1
        stat["tools"][event["tool"]] += 1
        stat["surfaces"][event["surface"]] += 1
        stat["origins"][event["origin"]] += 1
    return stats


def activation_counts(cutoff: float) -> dict | None:
    command = [
        "journalctl", "--quiet", "--no-pager", "--output=cat",
        "--since", f"@{int(cutoff)}",
    ]
    for unit in ACTIVATION_UNITS:
        command.extend(("--unit", unit))
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_activation_lines(result.stdout.splitlines())


def read_armed_state(path: Path = ARMED_STATE) -> dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        product: float(stamp)
        for product, stamp in raw.items()
        if product in ACTIVATION_PRODUCTS and isinstance(stamp, (int, float))
    }


def _counter_summary(values: Counter, limit: int = 3) -> str:
    return ", ".join(
        f"{name}:{count}" for name, count in values.most_common(limit))


def _activation_summary(product: str, activations: dict | None,
                        armed: dict[str, float], cutoff: float,
                        now: float, window_s: int) -> str:
    armed_at = armed.get(product)
    if armed_at is None:
        return f"{product}: n/a — telemetry not armed"
    coverage_s = max(0.0, now - max(cutoff, armed_at))
    coverage_h = min(window_s, coverage_s) / 3600
    coverage = "" if coverage_s >= window_s - 60 else f"; coverage {coverage_h:.1f}h"
    if activations is None:
        return f"{product}: n/a — journal query failed{coverage}"
    stat = activations.get(product, _activation_stat())
    detail = ""
    if stat["known"]:
        tools = _counter_summary(stat["tools"])
        surfaces = _counter_summary(stat["surfaces"])
        origins = _counter_summary(stat["origins"])
        detail = f"; origins {origins}; tools {tools}; surfaces {surfaces}"
    invalid = f"; invalid probes {stat['invalid']}" if stat["invalid"] else ""
    return (
        f"{product}: {stat['known']} known calls "
        f"({stat['success']} ok, {stat['error']} error){invalid}{detail}{coverage}"
    )


def render_digest(*, now: float, window_s: int, edge: dict,
                  activations: dict | None, armed: dict[str, float],
                  funnel: Counter | None) -> str:
    cutoff = now - window_s
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%d %b")
    hours = window_s / 3600
    lines = [f"📈 Fleet usage — {hours:g}h to {day} (UTC)",
             "Edge traffic (reachability; not tool usage):"]
    transport_posts = 0
    shown = set()
    for _host, _prefix, name in BUCKETS:
        if name in shown:
            continue
        shown.add(name)
        stat = edge.get(name)
        if not stat or not stat["req"]:
            continue
        if name.endswith("-mcp"):
            transport_posts += stat["post"]
        unknown = stat["req"] - stat["automation_req"]
        err = f", {stat['err']} errors" if stat["err"] else ""
        auto_detail = _counter_summary(stat["automation"])
        auto_detail = f" [{auto_detail}]" if auto_detail else ""
        lines.append(
            f"{name}: {stat['req']} req/{stat['post']} POST, "
            f"{len(stat['ips'])} IPs, known-auto {stat['automation_req']} req/"
            f"{stat['automation_post']} POST"
            f"{auto_detail}, unclassified {unknown}{err}")
    if len(lines) == 2:
        lines.append("no edge traffic recorded — check Caddy logging")
    lines.append(f"MCP transport POSTs: {transport_posts} (not activations)")

    lines.append("Actual MCP tool calls (post-dispatch):")
    lines.extend(_activation_summary(
        product, activations, armed, cutoff, now, window_s)
        for product in ACTIVATION_PRODUCTS)

    if funnel is not None:
        keys = ("probe", "unpaid", "free", "paid", "verify_fail", "settle_fail")
        shown_funnel = ", ".join(
            f"{key}:{funnel[key]}" for key in keys if funnel.get(key))
        extra = sum(value for key, value in funnel.items() if key not in keys)
        if extra:
            shown_funnel += (", " if shown_funnel else "") + f"other:{extra}"
        lines.append(f"groundcheck funnel: {shown_funnel or 'quiet'}")

    lines.append(
        "Known-auto is explicit monitoring/scanner traffic; unclassified is not "
        "assumed human. LiquiLens runs on Railway and is outside this host digest.")
    return "\n".join(lines)[:4000]


def read_token() -> str:
    path, variable = TOKEN_ENV
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(variable + "="):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("bot token not found")


def send_telegram(message: str) -> bool:
    data = urllib.parse.urlencode({
        "chat_id": OWNER_CHAT,
        "text": message,
        "disable_notification": "true",
    }).encode()
    token = read_token()
    with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, timeout=20) as response:
        return bool(json.load(response).get("ok"))


def build_digest(hours: float = 24, now: float | None = None) -> str:
    current = time.time() if now is None else now
    window_s = max(1, int(hours * 3600))
    cutoff = current - window_s
    return render_digest(
        now=current,
        window_s=window_s,
        edge=parse_log(cutoff),
        activations=activation_counts(cutoff),
        armed=read_armed_state(),
        funnel=funnel_counts(cutoff),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--print-only", action="store_true",
                        help="print the digest without sending Telegram")
    args = parser.parse_args(argv)
    message = build_digest(args.hours)
    if args.print_only:
        print(message)
        return 0
    ok = send_telegram(message)
    print(f"digest sent ok={ok} lines={len(message.splitlines())}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
