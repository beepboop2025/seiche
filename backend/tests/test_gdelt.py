"""GDELT sweep cache semantics: a partial sweep (mid-sweep 429) must carry
the missing topics over from the stale complete blob instead of clobbering
it with a fresh near-empty one."""

import asyncio

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
    out = asyncio.run(gdelt.fetch_all(None, faults))

    assert t0 in out["topics"] and out["topics"][t0]["volume"][0]["value"] == 9.0
    assert "stale" not in out["topics"][t0]
    # the stale sweep's other topics survive, marked
    assert out["topics"][t1]["stale"] is True
    assert out["topics"][t2]["volume"][0]["value"] == 3.0
    assert saved["gdelt:index"] == out
    assert any("rate-limited" in f.get("detail", "") for f in faults)
