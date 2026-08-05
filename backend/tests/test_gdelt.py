"""GDELT sweep cache semantics: a partial sweep (mid-sweep 429) must carry
the missing topics over from the stale complete blob instead of clobbering
it with a fresh near-empty one."""

import asyncio
from datetime import datetime, timezone
import gzip

from seiche.engines import scuttlebutt
from seiche.sources import gdelt


def test_partial_sweep_carries_stale_topics(monkeypatch):
    t0, t1, t2 = (t[0] for t in gdelt.SCUTTLEBUTT_TOPICS[:3])
    blobs = {"gdelt:index": {"fetched_at": "2026-07-17T00:00:00Z", "topics": {
        t0: {"label": t0, "volume": [{"date": "d", "value": 1.0}], "tone": []},
        t1: {"label": t1, "volume": [{"date": "d", "value": 2.0}], "tone": []},
        t2: {"label": t2, "volume": [{"date": "d", "value": 3.0}], "tone": []},
    }}}
    saved = {}
    # TTL'd loads (fresh-cache + cooldown probes) miss; the no-TTL stale load hits
    monkeypatch.setattr(gdelt.store, "load_blob",
                        lambda k, ttl=None: None if ttl is not None else blobs.get(k))
    monkeypatch.setattr(gdelt.store, "save_blob",
                        lambda k, v: saved.__setitem__(k, v))
    monkeypatch.setattr(gdelt, "GDELT_CALL_SPACING_S", 0)

    calls = {"n": 0}

    async def fake_mode(client, query, mode):
        calls["n"] += 1
        if calls["n"] <= 2:          # topic 1's volume + tone succeed
            return [{"date": "2026-07-18", "value": 9.0}]
        raise RuntimeError("429 rate limit exceeded")

    monkeypatch.setattr(gdelt, "_mode", fake_mode)

    faults: list[dict] = []
    out = asyncio.run(gdelt._fetch_legacy_doc(None, faults))

    assert t0 in out["topics"] and out["topics"][t0]["volume"][0]["value"] == 9.0
    assert "stale" not in out["topics"][t0]
    # the stale sweep's other topics survive, marked
    assert out["topics"][t1]["stale"] is True
    assert out["topics"][t2]["volume"][0]["value"] == 3.0
    assert saved["gdelt:index"] == out
    assert any("rate-limited" in f.get("detail", "") for f in faults)


def test_gdelt_base_env_override(monkeypatch):
    """GDELT_BASE reroutes the fetcher (box gdelt-gate); default is direct."""
    import importlib

    from seiche.sources import gdelt as g
    monkeypatch.setenv("GDELT_BASE", "http://127.0.0.1:8794")
    g2 = importlib.reload(g)
    assert g2.API == "http://127.0.0.1:8794/api/v2/doc/doc"
    monkeypatch.delenv("GDELT_BASE")
    g3 = importlib.reload(g2)
    assert g3.API.startswith("https://api.gdeltproject.org")


def test_web_ngram_batch_scans_all_topics_in_one_stream():
    raw = "\n".join([
        "1\tMoney market funds face withdrawals\t2",
        "1\tTreasury bills absorb cash\t1",
        "2\tStanding repo facility usage rose\t1",
        "3\tUnrelated global headline words\t1",
        "bad\tBasis trade line is ignored\t1",
    ]).encode()
    sample = gdelt._parse_web_batch(gzip.compress(raw), "20260805173200")

    assert sample["documents"] == 3
    assert sample["topic_counts"]["mmf"] == 1
    assert sample["topic_counts"]["bills"] == 1
    assert sample["topic_counts"]["facilities"] == 1
    assert sample["topic_counts"]["repo"] == 0
    assert sample["batch_at"] == "2026-08-05T17:32:00+00:00"


def test_web_fetch_persists_zeroes_as_valid_observations(monkeypatch):
    blobs = {}
    monkeypatch.setenv("GDELT_SOURCE_MODE", "web-ngrams")
    monkeypatch.setattr(gdelt.store, "load_blob",
                        lambda key, ttl=None: blobs.get(key) if ttl is None else None)
    monkeypatch.setattr(gdelt.store, "save_blob",
                        lambda key, value: blobs.__setitem__(key, value))

    async def sample(_client, asof=None):
        return {
            "batch_at": "2026-08-05T17:32:00+00:00",
            "documents": 1000,
            "topic_counts": {"mmf": 2},
            "url": "https://example.test/batch.gz",
            "compressed_bytes": 123,
        }

    monkeypatch.setattr(gdelt, "_fetch_web_sample", sample)
    faults = []
    out = asyncio.run(gdelt.fetch_all(None, faults))

    assert faults == []
    assert out["mode"] == "web-ngrams"
    assert set(out["topics"]) == {t[0] for t in gdelt.SCUTTLEBUTT_TOPICS}
    assert out["topics"]["mmf"]["current_share_pct"] == 0.2
    assert out["topics"]["repo"]["current_share_pct"] == 0.0
    assert blobs[gdelt.WEB_HISTORY_KEY]["samples"][0]["documents"] == 1000

    engine = scuttlebutt.analyze(out)
    assert engine["ok"] is True
    assert engine["source_mode"] == "web-ngrams"
    assert engine["baseline_ready"] is False
    assert engine["latest"]["n_topics"] == 6


def test_web_history_sidecar_survives_a_fresh_database(monkeypatch, tmp_path):
    """The static publisher can recover history on a disposable CI runner."""
    blobs = {}
    history_file = tmp_path / "gdelt" / "history.json"
    monkeypatch.setattr(gdelt, "WEB_HISTORY_FILE", str(history_file))
    monkeypatch.setattr(gdelt.store, "load_blob",
                        lambda key, ttl=None: blobs.get(key))
    monkeypatch.setattr(gdelt.store, "save_blob",
                        lambda key, value: blobs.__setitem__(key, value))
    sample = {
        "batch_at": "2026-08-05T17:32:00+00:00",
        "documents": 4479,
        "topic_counts": {"mmf": 1},
    }

    expected = gdelt._merge_web_sample({"samples": []}, sample)
    assert history_file.is_file()
    assert not history_file.with_name("history.json.tmp").exists()

    blobs.clear()  # simulate the next publish runner's empty SQLite database
    assert gdelt._load_web_history() == expected


def test_web_refresh_survives_caller_cancellation_and_runs_once(monkeypatch):
    blobs = {}
    calls = {"n": 0}
    monkeypatch.setenv("GDELT_SOURCE_MODE", "web-ngrams")
    monkeypatch.setattr(gdelt, "_web_refresh_task", None)
    monkeypatch.setattr(gdelt.store, "load_blob",
                        lambda key, ttl=None: blobs.get(key))
    monkeypatch.setattr(gdelt.store, "save_blob",
                        lambda key, value: blobs.__setitem__(key, value))

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_sample(_client, asof=None):
            calls["n"] += 1
            started.set()
            await release.wait()
            return {
                "batch_at": "2026-08-05T17:32:00+00:00",
                "documents": 4479,
                "topic_counts": {"mmf": 1},
            }

        monkeypatch.setattr(gdelt, "_fetch_web_sample", slow_sample)
        first = asyncio.create_task(gdelt.fetch_all(None, []))
        await started.wait()
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass

        shared = gdelt._web_refresh_task
        assert shared is not None and not shared.cancelled()
        release.set()
        await shared

        faults = []
        recovered = await gdelt.fetch_all(None, faults)
        assert faults == []
        assert recovered["topics"]["mmf"]["matched_documents"] == 1

    asyncio.run(scenario())
    assert calls["n"] == 1
    assert gdelt.WEB_INDEX_KEY in blobs


def test_web_fetch_uses_recent_lkg_without_claiming_freshness(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    history = {"schema": "seiche.gdelt-web-history.v1", "samples": [{
        "batch_at": now,
        "documents": 250,
        "topic_counts": {"repo": 1},
    }]}
    blobs = {gdelt.WEB_HISTORY_KEY: history}
    monkeypatch.setenv("GDELT_SOURCE_MODE", "web-ngrams")
    monkeypatch.setattr(gdelt.store, "load_blob",
                        lambda key, ttl=None: blobs.get(key) if ttl is None else None)
    monkeypatch.setattr(gdelt.store, "save_blob",
                        lambda key, value: blobs.__setitem__(key, value))

    async def broken(_client, asof=None):
        raise RuntimeError("bucket unavailable")

    monkeypatch.setattr(gdelt, "_fetch_web_sample", broken)
    faults = []
    out = asyncio.run(gdelt.fetch_all(None, faults))

    assert faults == []
    assert out["stale"] is True
    assert "last-known-good" in out["refresh_note"]
    assert out["topics"]["repo"]["matched_documents"] == 1


def test_web_cooldown_without_history_stays_a_visible_fault(monkeypatch):
    blobs = {gdelt.WEB_COOLDOWN_KEY: {"detail": "previous bucket failure"}}
    monkeypatch.setenv("GDELT_SOURCE_MODE", "web-ngrams")
    monkeypatch.setattr(gdelt.store, "load_blob",
                        lambda key, ttl=None: blobs.get(key))

    async def must_not_fetch(_client, asof=None):
        raise AssertionError("cooldown must prevent another upstream attempt")

    monkeypatch.setattr(gdelt, "_fetch_web_sample", must_not_fetch)
    faults = []
    out = asyncio.run(gdelt.fetch_all(None, faults))

    assert out["topics"] == {}
    assert faults == [{"source": "gdelt", "detail": "previous bucket failure"}]
