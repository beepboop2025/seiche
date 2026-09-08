"""Source clocks, pagination and untrusted catalog boundaries."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from seiche import research_network as network

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def catalog(count=30):
    return {"schema": network.CATALOG_SCHEMA, "generated_at": "2026-09-08T23:00:00Z", "datasets": [
        {"id": f"china-{i}", "name": f"China dataset {i}", "layer": "economy", "cadence": "PT6H",
         "artifacts": {"evidence_state": "fresh", "observed_at": "2026-09-08T22:00:00Z", "value": 999},
         "urls": {"latest": f"https://palimpsest.info/readings/china-{i}.json"},
         "license": {"name": "Source rights retained"}, "secret": "do not copy", "value": 999}
        for i in range(count)]}


def test_all_datasets_are_reachable_without_copying_values_or_mutating_source():
    raw = catalog(); prior = deepcopy(raw); ids = []
    offset = 0
    while offset is not None:
        result = network.project(raw, topic="china", offset=offset, evaluated_at=NOW)
        assert result["matched_total"] == result["catalog_total"] == 30
        ids.extend(row["id"] for row in result["datasets"])
        assert "secret" not in str(result) and "999" not in str(result)
        offset = result["next_offset"]
    assert len(set(ids)) == 30
    assert raw == prior


@pytest.mark.parametrize("state", ["gated", "private-node", "disabled", "warming", "stale"])
def test_restricted_and_missing_states_are_never_upgraded(state):
    raw = catalog(1); raw["datasets"][0]["artifacts"]["evidence_state"] = state
    result = network.project(raw, evaluated_at=NOW)
    assert result["datasets"][0]["evidence_state"] == state
    assert result["datasets"][0]["values_included"] is False
    assert result["eligibility"] == {"blend_into_score": False, "training": False, "execution": False}


def test_freshness_ages_without_rewriting_source_clock():
    raw = catalog(1); row = raw["datasets"][0]
    row["artifacts"]["observed_at"] = "2026-09-07T00:00:00Z"
    result = network.project(raw, evaluated_at=NOW)["datasets"][0]
    assert result["source_reported_state"] == "fresh"
    assert result["evidence_state"] == "stale"
    assert result["observed_at"] == "2026-09-07T00:00:00Z"


@pytest.mark.parametrize("clock", [None, "nonsense", "2026-09-10T00:00:00Z", "2026-09-08T23:00:00"])
def test_bad_catalog_clocks_do_not_establish_current_evidence(clock):
    raw = catalog(); raw["generated_at"] = clock
    result = network.project(raw, evaluated_at=NOW)
    assert result["status"] == "unavailable" and result["datasets"] == []
    assert result["next_steps"]


@pytest.mark.parametrize("url", ["https://evil.test/a", "javascript:alert(1)", "https://palimpsest.info@evil.test/a", "https://palimpsest.info/a?token=secret", "https://palimpsest.info:443/a"])
def test_catalog_cannot_expand_link_authority(url):
    raw = catalog(1); raw["datasets"][0]["urls"]["latest"] = url
    assert network.project(raw, evaluated_at=NOW)["datasets"][0]["data_url"] is None


@pytest.mark.parametrize("args", [{"url": "https://evil.test"}, {"topic": []}, {"limit": True}, {"limit": 26}, {"offset": -1}, {"offset": "0"}])
def test_selection_rejects_unbounded_or_ambiguous_inputs(args):
    with pytest.raises(ValueError): network.selection(args)


def test_failed_refresh_does_not_reuse_or_retimestamp_previous_catalog(monkeypatch):
    network._CACHE.clear()
    monkeypatch.setattr(network, "_fetch_catalog", lambda: (catalog(), "a" * 64, "2026-09-08T23:00:00Z"))
    network.read()
    network._CACHE["expires"] = 0
    def failure(): raise OSError("private transport details")
    monkeypatch.setattr(network, "_fetch_catalog", failure)
    result = network.read()
    assert result["status"] == "unavailable"
    assert result["source"]["retrieved_at"] is None
    assert "private transport details" not in str(result)
    network._CACHE.clear()



def test_rest_and_mcp_use_the_same_published_projection(monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api, mcp_server

    def read(args):
        return network.project(catalog(), topic=args["topic"], offset=args["offset"], limit=args["limit"], evaluated_at=NOW)
    monkeypatch.setattr(network, "read", read)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", lambda: None)
    arguments = {"topic": "china", "offset": 12, "limit": 12}
    response = TestClient(api.app).get("/api/v2/research-network", params=arguments)
    assert response.status_code == 200
    assert response.json() == mcp_server.tool_research_network(arguments, True)
    assert response.json()["funding_context"]["status"] == "unavailable"
    assert response.headers["Cache-Control"] == "no-store"
    assert "research_network" in mcp_server.OUTPUT_SCHEMAS
    assert "/api/v2/research-network" in api._public_openapi_document()["paths"]
