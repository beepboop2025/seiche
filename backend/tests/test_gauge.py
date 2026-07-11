"""/api/gauge: the free, versioned, machine-shaped regime reading."""

import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble


@pytest.fixture()
def client(monkeypatch, fake_snap):
    async def fake_snapshot(force=False):
        return fake_snap

    monkeypatch.setattr(assemble, "snapshot", fake_snapshot)
    return TestClient(api.app)


def test_gauge_is_public_versioned_and_thin(client, fake_snap):
    r = client.get("/api/gauge")
    assert r.status_code == 200
    g = r.json()
    assert g["schema"] == "seiche.gauge.v1"
    assert g["regime"] == "EROSION" and g["index"] == 41.0
    assert g["tell"] == 12.0
    assert g["faults"] == 0
    assert "not investment advice" in g["notes"]
    # thin means thin: no board internals leak through this surface
    assert "engines" not in g and "deep" not in g and "decomposition" not in str(g)


def test_gauge_survives_missing_sections(client, fake_snap, monkeypatch):
    async def bare_snapshot(force=False):
        return {"generated_at": "2026-07-10T00:00:00Z",
                "engines": {"composite": {}}, "faults": None}

    monkeypatch.setattr(assemble, "snapshot", bare_snapshot)
    r = client.get("/api/gauge")
    assert r.status_code == 200
    g = r.json()
    assert g["regime"] is None and g["next_turn"] is None and g["faults"] == 0
