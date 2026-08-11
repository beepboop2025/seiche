"""The buttery-serving layer: pre-serialized gzip/ETag overview responses and
the stale-while-revalidate snapshot cache. A reader must never pay the
assembly bill, and a poller must never re-download bytes it already has."""

import asyncio
import gzip
import json
import time

import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble


@pytest.fixture()
def client(monkeypatch, fake_snap):
    async def fake_snapshot(force=False):
        return fake_snap

    monkeypatch.setattr(assemble, "snapshot", fake_snapshot)
    # the wire cache keys on payload identity — drop state from other tests
    monkeypatch.setitem(api._OVERVIEW_WIRE, "src", None)
    return TestClient(api.app)


# ---- /api/overview wire format ------------------------------------------------

def test_overview_gzips_when_accepted(client, fake_snap):
    r = client.get("/api/overview", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # httpx transparently decodes; the wire headers tell the real story
    assert r.headers.get("content-encoding") == "gzip"
    assert r.headers["vary"] == "Accept-Encoding"
    assert r.json()["generated_at"] == fake_snap["generated_at"]


def test_overview_plain_when_gzip_not_accepted(client, fake_snap):
    r = client.get("/api/overview", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.json()["generated_at"] == fake_snap["generated_at"]


def test_overview_etag_roundtrip_304(client):
    first = client.get("/api/overview")
    etag = first.headers["etag"]
    assert etag.startswith('"')
    again = client.get("/api/overview", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert again.content == b""


def test_overview_cache_control_allows_short_shared_caching(client):
    r = client.get("/api/overview")
    cc = r.headers["cache-control"]
    assert "public" in cc and "max-age=60" in cc and "stale-while-revalidate" in cc


def test_overview_wire_serialized_once_per_payload(client, fake_snap):
    client.get("/api/overview")
    body_first = api._OVERVIEW_WIRE["body"]
    client.get("/api/overview")
    assert api._OVERVIEW_WIRE["body"] is body_first  # same bytes object reused
    # and the gzip really is the body
    assert json.loads(gzip.decompress(api._OVERVIEW_WIRE["gz"])) == json.loads(body_first)


def test_overview_answers_head_for_monitors(client):
    warm = client.get("/api/overview")
    r = client.head("/api/overview")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["etag"] == warm.headers["etag"]
    assert "max-age=60" in r.headers["cache-control"]


def test_public_and_gauge_carry_cache_control(client):
    assert "max-age=60" in client.get("/api/public").headers["cache-control"]
    assert "max-age=60" in client.get("/api/gauge").headers["cache-control"]


def test_health_reads_only_the_completed_snapshot(client, monkeypatch, fake_snap):
    async def boom(force=False):
        raise AssertionError("health must never call snapshot or its builder")

    monkeypatch.setattr(assemble, "snapshot", boom)
    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: fake_snap)

    r = client.get("/api/health")

    expected = {
        "generated_at": fake_snap["generated_at"],
        "version": fake_snap["version"],
        "faults": fake_snap["faults"],
        "provenance": fake_snap["provenance"],
    }
    assert r.status_code == 200
    assert r.json() == expected
    assert r.content == json.dumps(
        expected, ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert r.headers["cache-control"] == "no-store"


def test_health_cold_cache_is_immediately_unavailable(client, monkeypatch):
    async def boom(force=False):
        raise AssertionError("health must never start or join a snapshot build")

    monkeypatch.setattr(assemble, "snapshot", boom)
    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: None)

    r = client.get("/api/health")

    assert r.status_code == 503
    assert r.json() == {
        "status": "warming_or_unavailable",
        "version": assemble.VERSION_LABEL,
    }
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["retry-after"] == "10"
    assert "generated_at" not in r.json()
    assert "faults" not in r.json()
    assert "provenance" not in r.json()


def test_health_treats_a_stale_completed_snapshot_as_ready(
        client, clean_cache, monkeypatch, fake_snap):
    async def boom(force=False):
        raise AssertionError("health must not refresh a stale snapshot")

    monkeypatch.setattr(assemble, "snapshot", boom)
    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    assemble._cache.update(payload=fake_snap, at=0.0)

    r = client.get("/api/health")

    assert r.status_code == 200
    assert r.json()["generated_at"] == fake_snap["generated_at"]


def test_health_can_gate_on_a_snapshot_rebuilt_by_this_process(
        client, monkeypatch, fake_snap):
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: fake_snap)
    monkeypatch.setattr(assemble, "cached_snapshot_was_rebuilt", lambda: False)

    available = client.get("/api/health")
    candidate = client.get("/api/health?require_rebuilt=true")

    assert available.status_code == 200
    assert candidate.status_code == 503
    assert candidate.json() == {
        "status": "rebuilding_from_last_known_good",
        "version": assemble.VERSION_LABEL,
        "serving_generated_at": fake_snap["generated_at"],
    }
    assert candidate.headers["retry-after"] == "10"

    monkeypatch.setattr(assemble, "cached_snapshot_was_rebuilt", lambda: True)
    assert client.get("/api/health?require_rebuilt=true").status_code == 200


def test_asof_replay_is_cacheable_for_a_day(client, monkeypatch):
    async def fake_asof(date):
        return {"ok": True, "asof": date, "engines": {}}

    monkeypatch.setattr(assemble, "snapshot_asof", fake_asof)
    r = client.get("/api/asof/2025-03-14")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=86400"


# ---- stale-while-revalidate snapshot cache -------------------------------------

@pytest.fixture()
def clean_cache(monkeypatch):
    monkeypatch.setitem(assemble._cache, "payload", None)
    monkeypatch.setitem(assemble._cache, "at", 0.0)
    monkeypatch.setitem(assemble._cache, "source", None)
    monkeypatch.setattr(assemble, "_refreshing", False)


def test_fresh_cache_served_without_building(clean_cache, monkeypatch):
    async def boom():
        raise AssertionError("a fresh cache must never rebuild")

    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    fresh = {"generated_at": "fresh"}
    assemble._cache.update(payload=fresh, at=time.time())
    assert asyncio.run(assemble.snapshot()) is fresh


def test_cached_snapshot_is_passive_for_empty_and_stale_cache(
        clean_cache, monkeypatch):
    async def boom():
        raise AssertionError("cached_snapshot must never build")

    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    assert assemble.cached_snapshot() is None

    stale = {"generated_at": "stale"}
    assemble._cache.update(payload=stale, at=0.0)

    assert assemble.cached_snapshot() is stale


def test_restart_restores_durable_snapshot_as_stale_without_building(
        clean_cache, monkeypatch, fake_snap, tmp_path):
    async def boom():
        raise AssertionError("restart hydration must not build")

    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    monkeypatch.setattr(assemble.store, "load_blob", lambda key: fake_snap)
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", tmp_path / "missing.json")

    source = assemble.restore_cached_snapshot()

    assert source == "durable"
    assert assemble.cached_snapshot() is fake_snap
    assert assemble._cache["at"] == 0.0
    assert assemble.cached_snapshot_was_rebuilt() is False


def test_restart_falls_back_to_ci_snapshot_when_durable_copy_is_invalid(
        clean_cache, monkeypatch, fake_snap, tmp_path):
    static = tmp_path / "overview.json"
    static.write_text(json.dumps(fake_snap))
    monkeypatch.setattr(assemble.store, "load_blob", lambda key: {"bad": True})
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", static)

    assert assemble.restore_cached_snapshot() == "static"
    assert assemble.cached_snapshot()["generated_at"] == fake_snap["generated_at"]


def test_completed_snapshot_handoff_is_best_effort(monkeypatch, fake_snap):
    saved = []
    monkeypatch.setattr(
        assemble.store, "save_blob",
        lambda key, payload: saved.append((key, payload)),
    )

    assemble._persist_last_good_snapshot(fake_snap)

    assert saved == [(assemble.LAST_GOOD_SNAPSHOT_KEY, fake_snap)]

    def fail(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(assemble.store, "save_blob", fail)
    assemble._persist_last_good_snapshot(fake_snap)  # serving must continue


def test_production_lifespan_restores_before_background_refresh(monkeypatch):
    events = []

    def restore():
        events.append("restore")
        return "durable"

    async def refresh_forever():
        events.append("refresh")
        await asyncio.Event().wait()

    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setattr(assemble, "restore_cached_snapshot", restore)
    monkeypatch.setattr(api, "_keep_warm", refresh_forever)

    async def scenario():
        async with api._lifespan(api.app):
            for _ in range(20):
                if "refresh" in events:
                    break
                await asyncio.sleep(0)
            assert events == ["restore", "refresh"]

    asyncio.run(scenario())


def test_stale_cache_served_instantly_then_refreshed_once(clean_cache, monkeypatch):
    calls = []

    async def fake_build():
        calls.append(1)
        payload = {"generated_at": "rebuilt"}
        assemble._cache.update(payload=payload, at=time.time())
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)
    stale = {"generated_at": "stale"}
    assemble._cache.update(payload=stale, at=time.time() - assemble.CACHE_MIN * 60 - 1)

    async def scenario():
        got = await assemble.snapshot()          # must not block on the rebuild
        second = await assemble.snapshot()       # while refreshing: still stale, no 2nd task
        for _ in range(100):                     # let the background refresh land
            if calls:
                break
            await asyncio.sleep(0.01)
        after = await assemble.snapshot()
        return got, second, after

    got, second, after = asyncio.run(scenario())
    assert got is stale and second is stale
    assert calls == [1], "exactly one background rebuild"
    assert after["generated_at"] == "rebuilt"


def test_cold_cache_builds_inline(clean_cache, monkeypatch):
    async def fake_build():
        payload = {"generated_at": "cold-built"}
        assemble._cache.update(payload=payload, at=time.time())
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)
    assert asyncio.run(assemble.snapshot())["generated_at"] == "cold-built"
