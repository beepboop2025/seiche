"""REST twins of account-only MCP tools need the same bearer identity.

Over MCP, the tools in public_rights.account_tools() are offered only to a
verified bearer identity: desk_brief, ask_desk and replay_asof under the
research release, and positioning_book once the Book reopens. Their REST
routes (public_rights.ACCOUNT_ROUTES) must refuse an anonymous caller with 401
whatever SEICHE_BOARD_AUTH says, and serve an authenticated caller. The
research release opens funding_stress_forecast, so its twin /api/deep is
gated like the board that already shows the forecast probabilities.
"""
import inspect

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from seiche import accounts, ai, api, assemble, mcp_server, public_rights, usage

ROUTES = sorted({path for paths in public_rights.ACCOUNT_ROUTES.values() for path in paths})
TOKEN_ONLY = ("ask_desk", "desk_brief", "replay_asof", "positioning_book")
TOKEN_ROUTES = sorted(path for tool in TOKEN_ONLY for path in public_rights.ACCOUNT_ROUTES[tool])
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
    monkeypatch.setitem(api._OVERVIEW_WIRE, "src", None)
    accounts.add_user("desk_reader", "correct horse battery", tier="free")
    return TestClient(api.app)


@pytest.fixture
def book_open(monkeypatch):
    """Open the Book for one test, so /api/book shows its own identity gate."""
    monkeypatch.setattr(public_rights, "RESEARCH_RELEASE_WITHHELD_PATHS", frozenset())


def _token() -> str:
    return accounts.issue_token("desk_reader", "free")["token"]


def _gate(monkeypatch, gate):
    if gate is None:
        monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)
    else:
        monkeypatch.setenv("SEICHE_BOARD_AUTH", gate)


def test_every_account_tool_names_its_rest_twins_and_each_twin_follows_its_tool():
    assert set(public_rights.ACCOUNT_ROUTES) == public_rights.ACCOUNT_TOOLS
    assert public_rights.ACCOUNT_TOOLS == {
        name for name in public_rights.LEGACY_TOOLS if mcp_server.TOOLS[name][4] is False}
    routes = {route.path: route for route in api.app.routes if isinstance(route, APIRoute)}
    for tool, paths in public_rights.ACCOUNT_ROUTES.items():
        for path in paths:
            gates = [getattr(dependency.call, "account_tool", None)
                     for dependency in routes[path].dependant.dependencies]
            assert tool in gates, path


def test_the_active_account_set_follows_the_founder_decisions(book_open):
    assert public_rights.account_tools() == set(TOKEN_ONLY)
    assert "funding_stress_forecast" in public_rights.research_public_tools()


@pytest.mark.parametrize("gate", ["0", "1", None])
@pytest.mark.parametrize("path", TOKEN_ROUTES)
def test_anonymous_callers_get_401_on_every_token_only_twin(client, book_open, monkeypatch, path, gate):
    _gate(monkeypatch, gate)
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


@pytest.mark.parametrize("gate", ["0", "1", None])
def test_the_public_forecast_twin_is_gated_like_the_board(client, monkeypatch, gate):
    _gate(monkeypatch, gate)
    board, deep = client.get("/api/overview"), client.get("/api/deep")
    assert deep.status_code == board.status_code == (401 if gate == "1" else 200)
    if gate != "1":
        assert deep.json()["book"] == public_rights.withheld_book()
        assert deep.json()["ml"]["p_event_5bd"] == board.json()["deep"]["ml"]["p_event_5bd"]
    authed = client.get("/api/deep", headers={"Authorization": f"Bearer {_token()}"})
    assert authed.status_code == 200


def test_switching_back_withholds_every_twin_again(client, withheld_policy):
    for path in ROUTES:
        response = client.get(URLS.get(path, path), headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 403, path
        assert response.json()["category"] == "source_rights_restricted"


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
