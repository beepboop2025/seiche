"""Windfetch overlay: offline engine reads on a recorded pack shape, source
TTL/stale-serve semantics with a mocked HTTP layer, and the live-only
replay refusal. No network."""

from __future__ import annotations

import asyncio

import pytest

from seiche.engines import windfetch as eng
from seiche.sources import windfetch as src
from seiche.sources.base import SourceFault

pytestmark = pytest.mark.limit_memory("256 MB")

# Recorded shape of api.seiche.info/undertow/fetch.json (probed 2026-07-21),
# with planted ground truth: one funding-routed channel accruing, two world
# channels, one of which carries a qualifying percentile.
PACK = {
    "asof": "2026-07-21",
    "by_surface": {"seiche_funding": [], "undertow_markets": [], "liquilens_institutions": []},
    "channels": [
        {"channel": "funding_frictions", "name": "Funding frictions",
         "surface": "seiche_funding", "latest_surge": 1.31, "stress_pctl": None,
         "obs": 4, "mechanism": "dealer funding frictions transmit news to repo",
         "source_id": "arXiv:2603.10690", "source": "https://x/2603.10690",
         "note": "accruing history (n=4)"},
        {"channel": "energy_shock", "name": "Energy / commodity shock",
         "surface": "undertow_markets", "latest_surge": 1.24, "stress_pctl": None,
         "obs": 1, "mechanism": "energy shocks hit liquidity", "source_id": "arXiv:2607.16970",
         "source": "https://x/2607.16970", "note": "accruing history (n=1)"},
        {"channel": "geopolitical", "name": "Geopolitical risk",
         "surface": "undertow_markets", "latest_surge": 0.97, "stress_pctl": 0.42,
         "obs": 45, "mechanism": "GPR transmits via risk premia", "source_id": "arXiv:2606.07049",
         "source": "https://x/2606.07049", "note": None},
    ],
}


# ---------------------------------------------------------------- engine ----
def test_engine_routes_funding_slice_and_context():
    out = eng.analyze({"fetched_at": "2026-07-21T10:00:00Z", "pack": PACK})
    assert out["ok"] and out["overlay"] is True
    assert [c["channel"] for c in out["funding_channels"]] == ["funding_frictions"]
    assert len(out["world_context"]) == 2
    assert out["max_surge_any_channel"] == 1.31
    # doctrine must ride every payload
    assert "never enters the composite" in out["doctrine"]


def test_engine_passes_accrual_through_never_fills():
    out = eng.analyze({"pack": PACK})
    f = out["funding_channels"][0]
    assert f["accruing"] is True and f["stress_pctl"] is None
    g = next(c for c in out["world_context"] if c["channel"] == "geopolitical")
    assert g["accruing"] is False and g["stress_pctl"] == 0.42


def test_engine_states_empty_funding_slice():
    pack = {**PACK, "channels": [c for c in PACK["channels"]
                                 if c["surface"] != "seiche_funding"]}
    out = eng.analyze({"pack": pack})
    assert out["ok"] and out["funding_channels"] == []
    assert "absent read" in out["funding_channels_note"]


def test_engine_refuses_replay_before_pack_asof():
    out = eng.analyze({"pack": PACK, "replay_asof": "2026-07-01"})
    assert not out["ok"]
    assert "live-only" in out["reason"]
    # a replay AT the pack date is fine — the wind of today was recorded
    ok = eng.analyze({"pack": PACK, "replay_asof": "2026-07-21"})
    assert ok["ok"]


def test_engine_absent_pack_is_stated_never_calm():
    out = eng.analyze(None)
    assert not out["ok"] and "never calm" in out["reason"]


# ---------------------------------------------------------------- source ----
class _Resp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self._p


class _Client:
    def __init__(self, payload=None, fail=False):
        self._payload, self._fail = payload, fail
    async def get(self, *a, **k):
        if self._fail:
            raise RuntimeError("network down")
        return _Resp(self._payload)


def test_source_parse_rejects_malformed():
    with pytest.raises(ValueError):
        src.parse_pack([1, 2, 3])
    with pytest.raises(ValueError):
        src.parse_pack({"asof": "x"})  # no channels
    assert src.parse_pack(PACK) is PACK


def test_source_cold_failure_is_loud(monkeypatch):
    monkeypatch.setattr(src.store, "load_blob", lambda *a, **k: None)
    monkeypatch.setattr(src.store, "save_blob", lambda *a, **k: None)
    with pytest.raises(SourceFault):
        asyncio.run(src.fetch_all(_Client(fail=True), []))


def test_source_stale_serve_on_refresh_failure(monkeypatch):
    stale = {"fetched_at": "2026-07-20T00:00:00Z", "pack": PACK}
    calls = {"n": 0}
    def load_blob(key, ttl=None):
        calls["n"] += 1
        # first call (TTL-gated) says expired; second (stale rescue) serves
        return None if calls["n"] == 1 else stale
    monkeypatch.setattr(src.store, "load_blob", load_blob)
    monkeypatch.setattr(src.store, "save_blob", lambda *a, **k: None)
    faults: list = []
    out = asyncio.run(src.fetch_all(_Client(fail=True), faults))
    assert out is stale
    assert faults and faults[0]["source"] == "windfetch"


def test_source_fresh_fetch_caches(monkeypatch):
    saved = {}
    monkeypatch.setattr(src.store, "load_blob", lambda *a, **k: None)
    monkeypatch.setattr(src.store, "save_blob",
                        lambda key, payload: saved.update({key: payload}))
    out = asyncio.run(src.fetch_all(_Client(payload=PACK), []))
    assert out["pack"] is PACK and src.BLOB_KEY in saved
