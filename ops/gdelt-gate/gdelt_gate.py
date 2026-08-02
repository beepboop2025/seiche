#!/usr/bin/env python3
"""gdelt-gate: one polite GDELT client per box, shared by every consumer.

WHY. Three products fetch GDELT DOC 2.0 from this box's single IP (Seiche
scuttlebutt, Undertow world_events, LiquiLens news snapshot) and they have
been rate limiting EACH OTHER for weeks: Seiche never completed a 6 topic
sweep on the box and Undertow logged hard 429s even after its own cooldown
retry. GDELT limits per IP, so per process politeness cannot work. This
daemon serializes every upstream call behind one global spacing clock,
caches responses, coalesces identical in flight queries, converts GDELT's
"HTTP 200 but plaintext rate notice" lie into an honest 429, and refuses to
hammer upstream during a cooldown.

Consumers change ONE thing: point their base URL at
http://127.0.0.1:8794 instead of https://api.gdeltproject.org (env
GDELT_BASE in each repo, default unchanged so laptop runs still go direct).
Their own retry and cooldown logic keeps working because status codes pass
through faithfully.

Scope: this is a GDELT gate, not an open proxy. Only /api/v2/* paths are
forwarded, GET only, loopback only.

Stdlib only. Deploy: /opt/gdelt-gate/gdelt_gate.py + gdelt-gate.service.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("GDELT_GATE_UPSTREAM", "https://api.gdeltproject.org")
PORT = int(os.environ.get("GDELT_GATE_PORT", "8794"))
SPACING_S = float(os.environ.get("GDELT_GATE_SPACING_S", "6.5"))
MAX_WAIT_S = float(os.environ.get("GDELT_GATE_MAX_WAIT_S", "120"))
TTL_TIMELINE_S = float(os.environ.get("GDELT_GATE_TTL_TIMELINE_S", "10800"))
TTL_ARTLIST_S = float(os.environ.get("GDELT_GATE_TTL_ARTLIST_S", "900"))
COOLDOWN_S = float(os.environ.get("GDELT_GATE_COOLDOWN_S", "180"))
CACHE_MAX = int(os.environ.get("GDELT_GATE_CACHE_MAX", "512"))
UA = "gdelt-gate/1.0 (one polite client per box; +https://seiche.info)"


def _default_upstream(url: str, timeout: float = 45.0):
    """(status, content_type, body bytes). HTTPError becomes its status."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", "") if exc.headers else "", \
            exc.read() if hasattr(exc, "read") else b""


def is_plaintext_throttle(status: int, body: bytes) -> bool:
    """GDELT serves rate limit notices as HTTP 200 plain text."""
    if status != 200:
        return False
    head = body[:200].lstrip()
    if head.startswith(b"{") or head.startswith(b"["):
        return False
    return (b"limit requests" in body[:500].lower()
            or b"rate limit" in body[:500].lower()
            or not head.startswith(b"<"))  # non JSON, non HTML text reply


def ttl_for(path_qs: str) -> float:
    q = urllib.parse.urlparse(path_qs).query
    mode = (urllib.parse.parse_qs(q).get("mode") or [""])[0].lower()
    if mode.startswith("artlist"):
        return TTL_ARTLIST_S
    return TTL_TIMELINE_S


class Gate:
    """All policy in one testable object. Injectable clock and upstream."""

    def __init__(self, upstream=_default_upstream, now=time.monotonic,
                 sleep=time.sleep):
        self._upstream = upstream
        self._now = now
        self._sleep = sleep
        self._lock = threading.Lock()          # bucket + cache + inflight
        self._call_lock = threading.Lock()     # serializes upstream calls
        self._next_slot = 0.0
        self._cooldown_until = 0.0
        self._cache: dict[str, tuple[float, int, str, bytes]] = {}
        self._inflight: dict[str, threading.Event] = {}
        self.stats = {"served": 0, "cache_hits": 0, "coalesced": 0,
                      "upstream_calls": 0, "throttled_upstream": 0,
                      "refused_cooldown": 0, "refused_queue": 0}

    # ---------------------------------------------------------- helpers --
    def _cache_get(self, key: str):
        ent = self._cache.get(key)
        if not ent:
            return None
        stored, status, ctype, body = ent
        if self._now() - stored > ttl_for(key):
            return None
        return status, ctype, body

    def _cache_put(self, key: str, status: int, ctype: str, body: bytes):
        if status != 200:
            return
        self._cache[key] = (self._now(), status, ctype, body)
        while len(self._cache) > CACHE_MAX:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)

    def _reserve(self) -> float:
        """Reserve the next upstream slot, return how long to wait for it.
        A wait beyond MAX_WAIT_S is refused (returns negative)."""
        with self._lock:
            now = self._now()
            slot = max(now, self._next_slot)
            wait = slot - now
            if wait > MAX_WAIT_S:
                return -1.0
            self._next_slot = slot + SPACING_S
            return wait

    # ------------------------------------------------------------ core --
    def handle(self, path_qs: str) -> tuple[int, str, bytes]:
        self.stats["served"] += 1
        if not path_qs.startswith("/api/v2/"):
            return 404, "application/json", \
                b'{"error": "gdelt-gate forwards /api/v2/* only"}'
        cached = self._cache_get(path_qs)
        if cached:
            self.stats["cache_hits"] += 1
            return cached

        # coalesce identical in flight queries
        with self._lock:
            ev = self._inflight.get(path_qs)
            if ev is None:
                ev = threading.Event()
                self._inflight[path_qs] = ev
                leader = True
            else:
                leader = False
        if not leader:
            self.stats["coalesced"] += 1
            ev.wait(timeout=MAX_WAIT_S + 60)
            cached = self._cache_get(path_qs)
            if cached:
                return cached
            return 429, "application/json", \
                b'{"error": "coalesced request found no cached answer"}'

        try:
            if self._now() < self._cooldown_until:
                self.stats["refused_cooldown"] += 1
                return 429, "application/json", \
                    b'{"error": "gdelt-gate cooldown active, upstream was rate limited"}'
            wait = self._reserve()
            if wait < 0:
                self.stats["refused_queue"] += 1
                return 503, "application/json", \
                    b'{"error": "gdelt-gate queue full, retry later"}'
            if wait > 0:
                self._sleep(wait)
            with self._call_lock:
                self.stats["upstream_calls"] += 1
                status, ctype, body = self._upstream(UPSTREAM + path_qs)
            if status == 429 or is_plaintext_throttle(status, body):
                self.stats["throttled_upstream"] += 1
                with self._lock:
                    self._cooldown_until = self._now() + COOLDOWN_S
                print(f"upstream throttled, cooldown {COOLDOWN_S:.0f}s: "
                      f"{path_qs[:120]}", file=sys.stderr)
                return 429, "text/plain", body or b"rate limited"
            with self._lock:
                self._cache_put(path_qs, status, ctype or "application/json", body)
            return status, ctype or "application/json", body
        except Exception as exc:                             # noqa: BLE001
            print(f"upstream error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 502, "application/json", \
                json.dumps({"error": f"upstream {type(exc).__name__}"}).encode()
        finally:
            with self._lock:
                self._inflight.pop(path_qs, None)
            ev.set()

    def health(self) -> bytes:
        now = self._now()
        return json.dumps({
            "ok": True,
            "cooldown_active": now < self._cooldown_until,
            "cooldown_remaining_s": max(0.0, round(self._cooldown_until - now, 1)),
            "cache_entries": len(self._cache),
            "spacing_s": SPACING_S,
            "stats": self.stats,
        }, sort_keys=True).encode()


GATE = Gate()


class Handler(BaseHTTPRequestHandler):
    server_version = "gdelt-gate/1.0"

    def do_GET(self):                                        # noqa: N802
        if self.path == "/healthz":
            body = GATE.health()
            self._reply(200, "application/json", body)
            return
        status, ctype, body = GATE.handle(self.path)
        self._reply(status, ctype, body)

    def _reply(self, status: int, ctype: str, body: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", file=sys.stderr)


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"gdelt-gate listening on 127.0.0.1:{PORT}, spacing {SPACING_S}s, "
          f"upstream {UPSTREAM}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
