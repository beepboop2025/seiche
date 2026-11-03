"""REST twins of account-only MCP tools need the same bearer identity.

Over MCP, desk_brief, ask_desk, replay_asof, positioning_book and
funding_stress_forecast are offered only to a verified bearer identity. Their
REST routes (public_rights.ACCOUNT_ROUTES) must refuse an anonymous caller with
401 whatever SEICHE_BOARD_AUTH says, and serve an authenticated caller.
"""
import inspect

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from seiche import accounts, ai, api, assemble, mcp_server, public_rights, usage

ROUTES = sorted({path for paths in public_rights.ACCOUNT_ROUTES.values() for path in paths})
URLS = {
    "/api/asof/{date}": "/api/asof/2026-10-30",
    "/api/ask": "/api/ask?q=Is%20dollar%20funding%20tight%3F",
}
FORECAST_SOURCES = ("swell", "bathymetry", "ml", "markov", "oujump", "montecarlo")


@pytest.fixture
def client(tmp_path, monkeypatch, fake_snap, asof_snap):
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")

    async def snapshot(force=False):
        return fake_snap

    async def snapshot_asof(_date):
        return asof_snap

    async def answer(_question, _snapshot):
        return {"ok": True, "answer": "Overnight cash is orderly.",
                "grounding": "live board context pack", "route": "funding_stress_now"}

    monkeypatch.setattr(assemble, "snapshot", snapshot)
    monkeypatch.setattr(assemble, "snapshot_asof", snapshot_asof)
    monkeypatch.setattr(ai, "ask", answer)
    monkeypatch.setattr(api, "_ask_limiter", api._RateLimiter(10_000))
    accounts.add_user("desk_reader", "correct horse battery", tier="free")
    return TestClient(api.app)


@pytest.fixture
def book_open(monkeypatch):
    """Open the Book for one test, so /api/book shows its own identity gate."""
    monkeypatch.setattr(public_rights, "RESEARCH_RELEASE_WITHHELD_PATHS", frozenset())


def _token() -> str:
    return accounts.issue_token("desk_reader", "free")["token"]


def test_every_account_tool_names_its_rest_twins_and_they_require_identity():
    assert set(public_rights.ACCOUNT_ROUTES) == public_rights.ACCOUNT_TOOLS
    assert public_rights.ACCOUNT_TOOLS == {
        name for name in public_rights.LEGACY_TOOLS if mcp_server.TOOLS[name][4] is False}
    routes = {route.path: route for route in api.app.routes if isinstance(route, APIRoute)}
    for path in ROUTES:
        dependencies = {dependency.call for dependency in routes[path].dependant.dependencies}
        assert api.require_account in dependencies, path


@pytest.mark.parametrize("gate", ["0", "1", None])
@pytest.mark.parametrize("path", ROUTES)
def test_anonymous_callers_get_401_on_every_twin(client, book_open, monkeypatch, path, gate):
    if gate is None:
        monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)
    else:
        monkeypatch.setenv("SEICHE_BOARD_AUTH", gate)
    url = URLS.get(path, path)
    for headers in ({}, {"Authorization": "Bearer not-a-token"}):
        response = client.get(url, headers=headers)
        assert response.status_code == 401, (url, headers)
        assert response.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("path", ROUTES)
def test_authenticated_callers_are_served_by_every_twin(client, book_open, path):
    response = client.get(URLS.get(path, path), headers={"Authorization": f"Bearer {_token()}"})
    assert response.status_code == 200, response.text
    assert not public_rights.is_restriction(response.json() if path != "/api/brief" else {})


def test_the_withheld_book_answers_every_caller_with_its_restriction(client):
    for headers in ({}, {"Authorization": f"Bearer {_token()}"}):
        response = client.get("/api/book", headers=headers)
        assert response.status_code == 403
        assert response.json() == public_rights.legacy_unavailable("/api/book")


def test_the_engines_route_serves_no_forecast_tool_content(client, fake_snap, monkeypatch):
    """funding_stress_forecast reads deep blocks; /api/engines serves engines only."""
    source = inspect.getsource(mcp_server.tool_forecast)
    assert 'snap.get("deep"' in source and '"engines"' not in source
    board = {**fake_snap, "deep": {**fake_snap.get("deep", {}),
                                   **{name: {"ok": True, "sentinel": name} for name in FORECAST_SOURCES}}}

    async def snapshot(force=False):
        return board

    monkeypatch.setattr(assemble, "snapshot", snapshot)
    for name in FORECAST_SOURCES:
        assert name not in board["engines"]
        assert client.get(f"/api/engines/{name}").status_code == 404
