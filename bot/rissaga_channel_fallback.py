#!/usr/bin/env python3
"""Deterministic second chance for the Rissaga shared channel.

Hermes and this service are one logical channel owner. Hermes gets the first
chance to publish each selected desk read. This fallback runs ten minutes
later and fills only quota-failed gaps, using the same per-revision marker.
It never generates commentary and never reads a Telegram token.
"""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


LATEST_PATH = os.environ.get("RISSAGA_LATEST", "/var/lib/rissaga/latest.json")
MARKER_PATH = os.environ.get(
    "RISSAGA_POSTED",
    "/home/hermes/.hermes/state/rissaga_posted.json",
)
HELPER_PATH = os.environ.get(
    "RISSAGA_CHANNEL_HELPER",
    "/usr/local/bin/lab-channel-post",
)
MAX_AGE = timedelta(hours=8)
MAX_CANDIDATES = 2


class HandoffError(ValueError):
    """The producer handoff cannot safely authorize a channel post."""


def _read_json(path: str, missing=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise HandoffError("handoff missing")
    except (OSError, json.JSONDecodeError) as exc:
        raise HandoffError(f"unreadable JSON: {type(exc).__name__}") from exc


def _aware_timestamp(raw) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise HandoffError("generated timestamp missing")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffError("generated timestamp invalid") from exc
    if stamp.tzinfo is None:
        raise HandoffError("generated timestamp lacks timezone")
    return stamp


def load_handoff(now: datetime) -> dict:
    payload = _read_json(LATEST_PATH)
    if not isinstance(payload, dict) or payload.get("schema") != "rissaga.news.v2":
        raise HandoffError("v2 handoff required")
    if payload.get("channel_mode") != "hermes":
        raise HandoffError("channel mode must be hermes")
    generated = _aware_timestamp(payload.get("generated"))
    age = now - generated.astimezone(now.tzinfo or timezone.utc)
    if age < timedelta(0) or age > MAX_AGE:
        raise HandoffError("handoff stale or future dated")
    if not isinstance(payload.get("items"), list):
        raise HandoffError("items must be a list")
    return payload


def _required_string(obj: dict, name: str) -> str:
    value = obj.get(name)
    if not isinstance(value, str) or not value.strip():
        raise HandoffError(f"{name} missing")
    return value


def _bounded(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) > limit:
        value = value[:limit - 3].rstrip() + "..."
    return value


def _delivery_key(item: dict, route: dict) -> str:
    identity = item.get("dispatch_id")
    if not isinstance(identity, str) or not identity:
        identity = _required_string(item, "story_id")
    desk = _required_string(route, "desk")
    return f"{identity}:{desk}"


def select_candidates(payload: dict) -> list[dict]:
    selected = []
    for index, item in enumerate(payload["items"]):
        if not isinstance(item, dict):
            raise HandoffError("item must be an object")
        routes = item.get("routes")
        if not isinstance(routes, list):
            raise HandoffError("routes must be a list")
        flagged = []
        for route in routes:
            if not isinstance(route, dict):
                raise HandoffError("route must be an object")
            if route.get("channel_candidate") is True:
                desk = _required_string(route, "desk").strip().upper()
                if desk == "CRYPTO":
                    raise HandoffError(
                        "crypto route belongs to its dedicated channel"
                    )
                if desk in {"PALIMPSEST", "RIPTIDE", "CORPORATE", "REALECON"}:
                    raise HandoffError(
                        "side desk is not a shared-channel candidate"
                    )
                flagged.append(route)
        if len(flagged) > 1:
            raise HandoffError("more than one channel route on an item")
        if flagged:
            route = flagged[0]
            selected.append({
                "index": index,
                "item": item,
                "route": route,
                "key": _delivery_key(item, route),
            })
    if len(selected) > MAX_CANDIDATES:
        raise HandoffError("more than two channel routes globally")
    return selected


def load_marker() -> dict:
    marker = _read_json(MARKER_PATH, missing={})
    if not isinstance(marker, dict):
        raise HandoffError("delivery marker must be an object")
    posted = marker.get("posted")
    if posted is None:
        marker["posted"] = {}
    elif not isinstance(posted, dict):
        raise HandoffError("delivery marker posted field must be an object")
    return marker


def pending_candidates(payload: dict, selected: list[dict], marker: dict):
    if marker.get("generated") == payload["generated"]:
        return [], len(selected)
    pending = [candidate for candidate in selected
               if candidate["key"] not in marker["posted"]]
    return pending, len(selected) - len(pending)


def _without_long_dashes(value: str) -> str:
    value = value.replace("\u2014", ",").replace("\u2013", ",")
    return " ".join(value.split())


def _escaped(value: str) -> str:
    return html.escape(_without_long_dashes(value), quote=True)


def _safe_link(raw) -> str | None:
    if (not isinstance(raw, str) or not raw
            or len(raw) > 768
            or any(ch.isspace() or ord(ch) < 32 for ch in raw)):
        return None
    try:
        parsed = urllib.parse.urlsplit(raw)
        _ = parsed.port
    except ValueError:
        return None
    if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
        return None
    return raw


def compose(candidate: dict) -> str:
    item, route = candidate["item"], candidate["route"]
    desk = _required_string(route, "desk")
    label = _bounded(_required_string(route, "label"), 80)
    desk_nice = _bounded(_required_string(route, "desk_nice"), 80)
    desk_line = _bounded(_required_string(route, "desk_line"), 600)
    angle = _bounded(_required_string(route, "angle"), 400)
    fallback = _required_string(route, "fallback_commentary")
    if ("\u2014" in fallback or "\u2013" in fallback
            or "\n" in fallback or "\r" in fallback):
        raise HandoffError("fallback commentary breaks prose rules")
    fallback = _bounded(fallback, 400)
    title = _bounded(_required_string(item, "title"), 300)
    source = _bounded(_required_string(item, "source"), 120)
    age = _bounded(_required_string(item, "age"), 40)
    n_sources = item.get("n_sources")
    if isinstance(n_sources, bool) or not isinstance(n_sources, int) or n_sources < 1:
        raise HandoffError("n_sources must be a positive integer")

    heading = (f"\U0001f30a <b>Rissaga</b> "
               f"[{_escaped(desk)} \u00b7 {_escaped(label)}]")
    link = _safe_link(item.get("link"))
    escaped_title = _escaped(title)
    title_line = (f'<a href="{_escaped(link)}">{escaped_title}</a>'
                  if link else escaped_title)
    extra = n_sources - 1
    source_line = (f"{_escaped(source)} plus {extra} more outlets, {_escaped(age)}"
                   if extra else f"{_escaped(source)}, {_escaped(age)}")
    message = "\n".join((
        heading,
        "",
        "<b>WHAT HAPPENED</b>",
        title_line,
        source_line,
        "",
        "<b>WHY THIS DESK CARES</b>",
        html.escape(fallback, quote=True),
        "",
        "<b>LIVE DESK CHECK</b>",
        f"{_escaped(desk_nice)}: {_escaped(desk_line)}",
        "",
        "<b>WHAT TO WATCH NEXT</b>",
        _escaped(angle),
    ))
    if "\u2014" in message or "\u2013" in message:
        raise HandoffError("composed message breaks prose rules")
    return message


def _fsync_directory(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def save_marker(marker: dict) -> None:
    parent = os.path.dirname(MARKER_PATH) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".rissaga-posted.", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(marker, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, MARKER_PATH)
        os.chmod(MARKER_PATH, 0o600)
        _fsync_directory(parent)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


@contextmanager
def delivery_lock():
    parent = os.path.dirname(MARKER_PATH) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    lock_path = MARKER_PATH + ".lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def publish(text: str) -> bool:
    fd, path = tempfile.mkstemp(prefix="rissaga-channel-", suffix=".html")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            result = subprocess.run(
                [HELPER_PATH, "--text-file", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"channel helper failed: {type(exc).__name__}", file=sys.stderr)
            return False
        if result.returncode != 0:
            print(f"channel helper exited {result.returncode}", file=sys.stderr)
            return False
        return True
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _counts(candidates: int, already: int, pending: int, posted: int = 0) -> None:
    print(json.dumps({"already_posted": already, "candidates": candidates,
                      "pending": pending, "posted": posted}, sort_keys=True))


def run(dry_run: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if dry_run:
        payload = load_handoff(now)
        selected = select_candidates(payload)
        marker = load_marker()
        pending, already = pending_candidates(payload, selected, marker)
        for candidate in pending:
            compose(candidate)
        _counts(len(selected), already, len(pending))
        return 0

    # Hold one lock across selection, helper delivery and each durable marker
    # update. Overlapping fallback timers can never race the shared owner state.
    with delivery_lock():
        payload = load_handoff(now)
        selected = select_candidates(payload)
        marker = load_marker()
        pending, already = pending_candidates(payload, selected, marker)
        prepared = [(candidate, compose(candidate)) for candidate in pending]
        posted = 0
        for candidate, message in prepared:
            if not publish(message):
                _counts(len(selected), already, len(pending) - posted, posted)
                return 1
            marker["posted"][candidate["key"]] = payload["generated"]
            save_marker(marker)
            posted += 1
        _counts(len(selected), already, len(pending) - posted, posted)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rissaga channel fallback")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(dry_run=args.dry_run)
    except HandoffError as exc:
        print(f"rissaga fallback refused: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"rissaga fallback failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
