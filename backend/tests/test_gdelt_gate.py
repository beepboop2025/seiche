"""Offline tests for gdelt-gate: spacing, cache, coalescing, honest 429s,
cooldown, queue refusal. Injectable clock and upstream, no network."""

import os
import sys
import threading
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "ops", "gdelt-gate"))

import gdelt_gate as gg  # noqa: E402

DOC = "/api/v2/doc/doc?query=repo&mode=timelinevol&format=json"
ART = "/api/v2/doc/doc?query=repo&mode=artlist&format=json"
GOOD = (200, "application/json", b'{"timeline": []}')


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class FakeUpstream:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    def __call__(self, url, timeout=45.0):
        self.calls.append(url)
        for frag, reply in self.replies.items():
            if frag in url:
                return reply
        return GOOD


@pytest.fixture
def gate():
    clock = FakeClock()
    up = FakeUpstream()
    g = gg.Gate(upstream=up, now=clock.now, sleep=clock.sleep)
    return g, clock, up


def test_path_allowlist(gate):
    g, _, up = gate
    status, _, _ = g.handle("/etc/passwd")
    assert status == 404 and up.calls == []
    status, _, _ = g.handle("/api/v2/doc/doc?query=x")
    assert status == 200


def test_spacing_between_upstream_calls(gate):
    g, clock, up = gate
    g.handle(DOC)
    g.handle(DOC.replace("repo", "mmf"))
    assert len(up.calls) == 2
    assert clock.slept and clock.slept[-1] == pytest.approx(gg.SPACING_S)


def test_cache_hit_skips_upstream(gate):
    g, _, up = gate
    g.handle(DOC)
    status, _, body = g.handle(DOC)
    assert status == 200 and body == GOOD[2]
    assert len(up.calls) == 1
    assert g.stats["cache_hits"] == 1


def test_cache_expires_by_mode(gate):
    g, clock, up = gate
    g.handle(ART)
    clock.t += gg.TTL_ARTLIST_S + 1
    g.handle(ART)
    assert len(up.calls) == 2


def test_ttl_for_mode():
    assert gg.ttl_for(ART) == gg.TTL_ARTLIST_S
    assert gg.ttl_for(DOC) == gg.TTL_TIMELINE_S


def test_plaintext_throttle_becomes_429_and_cooldown(gate):
    g, clock, up = gate
    up.replies["query=hot"] = (200, "text/plain",
                               b"You have exceeded the daily limit requests.")
    status, _, _ = g.handle("/api/v2/doc/doc?query=hot&mode=timelinevol")
    assert status == 429
    status, _, _ = g.handle(DOC)
    assert status == 429
    assert g.stats["refused_cooldown"] == 1
    assert len(up.calls) == 1
    clock.t += gg.COOLDOWN_S + 1
    status, _, _ = g.handle(DOC)
    assert status == 200


def test_real_429_passes_through_and_cools_down(gate):
    g, _, up = gate
    up.replies["query=hot"] = (429, "text/plain", b"Too Many Requests")
    status, _, _ = g.handle("/api/v2/doc/doc?query=hot")
    assert status == 429
    assert g.stats["throttled_upstream"] == 1


def test_error_pages_are_not_cached(gate):
    g, _, up = gate
    up.replies["query=err"] = (500, "text/html", b"<html>boom</html>")
    status, _, _ = g.handle("/api/v2/doc/doc?query=err&mode=timelinevol")
    assert status == 500
    g.handle("/api/v2/doc/doc?query=err&mode=timelinevol")
    assert len(up.calls) == 2


def test_queue_refusal_when_wait_exceeds_max(gate, monkeypatch):
    g, clock, up = gate
    monkeypatch.setattr(gg, "MAX_WAIT_S", 10.0)
    monkeypatch.setattr(gg, "SPACING_S", 9.0)
    assert g.handle(DOC)[0] == 200
    assert g.handle("/api/v2/doc/doc?query=b")[0] == 200      # waits 9s
    clock.t -= 9.0   # rewind so the third projected wait exceeds the cap
    status, _, _ = g.handle("/api/v2/doc/doc?query=c")
    assert status == 503
    assert g.stats["refused_queue"] == 1


def test_is_plaintext_throttle_shapes():
    assert gg.is_plaintext_throttle(200, b"  please limit requests to one per 5s")
    assert not gg.is_plaintext_throttle(200, b'{"timeline": []}')
    assert not gg.is_plaintext_throttle(200, b"<html>an error page</html>")
    assert not gg.is_plaintext_throttle(500, b"whatever")


def test_coalescing_two_identical_requests():
    calls = []

    def slow_upstream(url, timeout=45.0):
        calls.append(url)
        time.sleep(0.15)
        return GOOD

    g = gg.Gate(upstream=slow_upstream, now=time.monotonic,
                sleep=lambda s: None)
    results = []

    def worker():
        results.append(g.handle(DOC)[0])

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    time.sleep(0.02)
    t2.start()
    t1.join()
    t2.join()
    assert results == [200, 200]
    assert len(calls) == 1
    assert g.stats["coalesced"] + g.stats["cache_hits"] >= 1


def test_health_reports():
    g = gg.Gate(upstream=FakeUpstream(), now=lambda: 5.0, sleep=lambda s: None)
    body = g.health()
    assert b'"ok": true' in body
