"""While the Book is withheld, no public payload or static export carries it.

The Book stays withheld for chain-integrity reasons: its static ledger is a
hash-chained file the publisher extends from the live copy. Its content is then
withheld wherever it appears, as a field-level projection carrying the
/api/book restriction: deep.book in every board payload, the Book positions in
as-published records, Book alerts, positioning_book and the static exports.

The board, the as-published records and both alert logs are seeded with Book
values that occur nowhere else. Every GET route and every MCP tool, anonymous
and authenticated, and the static publish steps run with the network closed; no
output may contain a Book value.
"""
from __future__ import annotations

import importlib.util
import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from seiche import (accounts, ai, alerts, ampleness, api, assemble, badge, brief, mcp_server,
                    prerender, public_rights, publisher, referee, skeptic, store, usage)

ROOT = Path(__file__).resolve().parents[2]
STANCE, VERDICT, CAVEAT, SINCE = "sentinel-stance-9q4", "sentinel-verdict-2k8", "sentinel-caveat-5m1", "2031-11-17"
WEIGHT, P_BOOK, DISPERSION, SHARPE, CUM = 0.6173, 0.08719, 0.02857, 1.4142, 7.3191
SENTINELS = (STANCE, VERDICT, CAVEAT, SINCE, "0.6173", "0.08719", "0.02857", "1.4142", "7.3191")
# A value for every path parameter in app.routes; a new parameter name fails
# the sweep until it is given one, so no new route escapes it.
PATH_VALUES = {
    "market_id": "US-USD", "timestamp": "2026-11-03T00:00:00Z", "mnemonic": "SOFR",
    "date": "2026-10-30", "name": "composite", "slug": "latest", "country": "JP",
    "room_id": "room-1", "stream": "stress_readings", "request_id": "x", "member": "x",
    "sha256": "0" * 64,
}
# Arguments for MCP tools that require some; everything else is called bare.
TOOL_ARGUMENTS = {
    "quantity_kg": "1", "fineness": "0.995", "price_usd_per_oz": "2400", "annual_rate_pct": "5",
    "fx_inr_per_usd": "84", "fees_usd": "0", "days": 30, "public_key_hex": "00" * 32,
    "room_id": "room-1", "event": {}, "client_signature_hex": "00" * 64, "date": "2026-10-30",
    "question": "What is the Book's stance today?",
}


def book_board(fake_snap: dict) -> dict:
    board = deepcopy(fake_snap)
    board["deep"]["book"] = {
        "ok": True, "asof": SINCE,
        "today": {"stance": STANCE, "rationale": "sentinel", "p_ensemble": P_BOOK,
                  "dispersion": DISPERSION, "positions": [{"sleeve": "cash", "weight": WEIGHT}]},
        "backtest": {"sharpe": SHARPE, "verdict": VERDICT, "episodes": []},
        "live": {"n_days": 3, "since": SINCE, "cum_return_pct": CUM},
        "caveats": [CAVEAT],
    }
    board["system"] = {"alert_log": {"status": "ready", "evaluated_at": board["generated_at"], "alerts": [
        {"fired_at": board["generated_at"], "rule": "book_flip", "state": f"{STANCE}:cash{WEIGHT:+}",
         "message": f"the Book is {STANCE} (cash{WEIGHT:+}), dispersion {DISPERSION}"},
        {"fired_at": board["generated_at"], "rule": "regime_change", "state": "EROSION",
         "message": "regime EROSION"},
    ]}}
    return board


RECORD_DAY = "2031-11-20"  # the record's own date is not Book content


def pit_record() -> dict:
    return {"date": RECORD_DAY, "value": 41.0, "regime": "EROSION",
            "book": {"stance": STANCE, "p_ensemble": P_BOOK, "dispersion": DISPERSION,
                     "positions": [["cash", WEIGHT]]}}


def leaks(text: str) -> list[str]:
    return sorted({sentinel for sentinel in SENTINELS if sentinel in text})


def _script(name: str):
    path = ROOT / "backend" / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"book_sweep_{name.removesuffix('.py')}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def seeded(tmp_path, monkeypatch, fake_snap, asof_snap):
    database = tmp_path / "seiche.sqlite"
    for module in (store, api, alerts, brief, badge):
        monkeypatch.setattr(module, "DB_PATH", database)
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    monkeypatch.setattr(api, "_mcp_limiter", api._RateLimiter(10_000))
    monkeypatch.setattr(api, "_ask_limiter", api._RateLimiter(10_000))
    board = book_board(fake_snap)

    async def snapshot(force=False):
        return board

    async def snapshot_asof(_date):
        return asof_snap

    async def answer(_question, snap):
        # Echo the context pack the assistant was given, so a Book value
        # handed to the model would surface in the answer.
        return {"ok": True, "answer": json.dumps(ai.context_pack(snap), default=str),
                "grounding": "context pack", "route": "funding_stress_now"}

    monkeypatch.setattr(assemble, "snapshot", snapshot)
    monkeypatch.setattr(assemble, "snapshot_asof", snapshot_asof)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: board)
    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: board)
    monkeypatch.setattr(mcp_server, "_get_completed_snapshot", lambda: board)
    monkeypatch.setattr(mcp_server, "_get_in_memory_completed_snapshot", lambda: board)
    monkeypatch.setattr(mcp_server, "_get_asof", lambda _date: asof_snap)
    monkeypatch.setattr(ai, "ask", answer)

    store.save_blob(f"pit:{RECORD_DAY}", pit_record())
    with alerts._conn() as conn:
        alerts._record(conn, "book_flip", f"{STANCE}:cash{WEIGHT:+}", f"the Book is {STANCE}")

    def closed(*_args, **_kwargs):
        raise OSError("network closed for the Book sweep")

    monkeypatch.setattr(socket.socket, "connect", closed)
    monkeypatch.setattr(socket.socket, "connect_ex", closed)
    accounts.add_user("book_sweep", "correct horse battery", tier="free")
    return board


def _bearer() -> dict:
    return {"Authorization": "Bearer " + accounts.issue_token("book_sweep", "free")["token"]}


def test_the_fixture_really_holds_book_values(seeded):
    assert set(leaks(json.dumps(seeded))) == set(SENTINELS)
    assert leaks(json.dumps(store.load_pit_records()))
    assert public_rights.book_withheld()


def test_no_get_route_serves_a_book_value(seeded):
    client = TestClient(api.app, raise_server_exceptions=False)
    routes = [route for route in api.app.routes if isinstance(route, APIRoute) and "GET" in route.methods]
    assert len(routes) >= 50
    exposed = {}
    for route in routes:
        path = route.path.format(**{name: PATH_VALUES[name] for name in route.param_convertors})
        if path == "/api/ask":
            path += "?q=What%20is%20the%20Book%27s%20stance%3F"
        for caller, headers in (("anonymous", {}), ("authenticated", _bearer())):
            response = client.get(path, headers=headers)
            found = leaks(response.text)
            if found:
                exposed[f"{caller} {path}"] = (response.status_code, found)
    assert exposed == {}


def test_book_fields_carry_the_explicit_withheld_state(seeded):
    client = TestClient(api.app)
    withheld = public_rights.withheld_book()
    assert withheld == public_rights.legacy_unavailable("/api/book")
    overview = client.get("/api/overview").json()
    assert overview["deep"]["book"] == withheld
    book_alert, other = overview["system"]["alert_log"]["alerts"]
    assert book_alert["withheld"] == withheld and book_alert["message"] is None
    assert book_alert["state"] is None and other["message"] == "regime EROSION"
    assert client.get("/api/deep", headers=_bearer()).json()["book"] == withheld
    assert [record["book"] for record in client.get("/api/pit").json()["records"]] == [withheld]
    rows = client.get("/api/alerts").json()["alerts"]
    assert rows and all(row["withheld"] == withheld for row in rows if row["rule"] == "book_flip")
    assert client.get("/api/book", headers=_bearer()).json() == withheld
    # The stored board itself is unchanged; only public copies are projected.
    assert seeded["deep"]["book"]["today"]["stance"] == STANCE


def test_no_mcp_tool_serves_a_book_value(seeded):
    client = TestClient(api.app, raise_server_exceptions=False)
    for headers in ({}, _bearer()):
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers=headers)
        assert not leaks(listed.text)
        assert "positioning_book" not in {tool["name"] for tool in listed.json()["result"]["tools"]}
    exposed = {}
    for name, spec in mcp_server.TOOLS.items():
        schema = spec[2] or {}
        base = {key: TOOL_ARGUMENTS[key] for key in schema.get("required", [])}
        calls = [base]
        for key, prop in (schema.get("properties") or {}).items():
            calls.extend({**base, key: value} for value in prop.get("enum", []) if key not in base)
        for caller, headers in (("anonymous", {}), ("authenticated", _bearer())):
            for arguments in calls:
                response = client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
                found = leaks(response.text)
                if found:
                    exposed[f"{caller} {name} {json.dumps(arguments, sort_keys=True)}"] = found
    assert exposed == {}


def test_positioning_book_returns_the_book_restriction(seeded):
    result = mcp_server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "positioning_book", "arguments": {}}},
                                 public=False, identity={"username": "book_sweep"})["result"]
    assert result["isError"] is True
    assert result["structuredContent"] == public_rights.legacy_unavailable("positioning_book")


def test_no_static_export_carries_a_book_value(seeded, tmp_path):
    pytest.importorskip("PIL")
    from seiche import social_cards

    site = tmp_path / "dist"
    data = site / "data"
    captured = tmp_path / "captured.json"
    captured.write_text(json.dumps(seeded))
    private = tmp_path / "private" / "board.json"
    assert _script("export_public.py").main([
        "--snapshot", str(captured), "--private-snapshot", str(private),
        str(data / "public.json"), str(data / "overview.json"),
    ]) == 0
    record = {"date": SINCE, "stance": STANCE, "positions": [["cash", WEIGHT]],
              "p_ensemble": P_BOOK, "dispersion": DISPERSION, "index": 41.0, "regime": "EROSION"}
    previous = tmp_path / "previous_book.json"
    previous.write_text(json.dumps([publisher.get_publisher().publish(record, [])]))
    assert _script("append_book_record.py").main([
        "--snapshot", str(data / "overview.json"), str(previous), str(data / "book_history.json"),
    ]) == 0
    published = skeptic.load_snapshot(data / "overview.json")
    skeptic.write_skeptic(published, site / "skeptic" / "index.html")
    ampleness.write_ampleness(published, site / "ampleness" / "index.html")
    referee.write_referee(published, site / "referee" / "index.html")
    (site / "dispatches").mkdir(parents=True)
    (site / "dispatches" / "index.json").write_text("[]")
    shell = (ROOT / "frontend" / "index.html").read_text()
    (site / "index.html").write_text(shell.replace(
        "</head>", '<link rel="stylesheet" href="/assets/index-test.css" />\n</head>', 1))
    assert prerender.build(site) >= 1000
    social_cards.build(site)

    files = {path.relative_to(site).as_posix(): path.read_text(encoding="utf-8")
             for path in sorted(site.rglob("*"))
             if path.is_file() and path.suffix in {".html", ".json", ".xml", ".txt", ".md", ".svg"}}
    assert {"data/overview.json", "data/public.json", "data/book_history.json", "index.html"} <= set(files)
    assert {name: leaks(body) for name, body in files.items() if leaks(body)} == {}
    overview = json.loads(files["data/overview.json"])
    assert overview["deep"]["book"] == public_rights.withheld_book()
    assert json.loads(files["data/book_history.json"]) == public_rights.legacy_unavailable(
        "/data/book_history.json")
    # Only the private board, outside the site, keeps the Book.
    assert leaks(private.read_text())


def test_book_alerts_are_not_raised_while_the_book_is_withheld(seeded, monkeypatch):
    rules = {rule for rule, _state, _message in alerts._candidates(seeded)}
    assert "book_flip" not in rules
    monkeypatch.setattr(public_rights, "RESEARCH_RELEASE_WITHHELD_PATHS", frozenset())
    assert "book_flip" in {rule for rule, _state, _message in alerts._candidates(seeded)}


def test_an_open_book_is_served_as_computed(seeded, monkeypatch):
    """The projection follows the Book's own policy; it is not a second rule."""
    monkeypatch.setattr(public_rights, "RESEARCH_RELEASE_WITHHELD_PATHS", frozenset())
    assert public_rights.project_book(seeded) is seeded
    client = TestClient(api.app)
    assert client.get("/api/overview").json()["deep"]["book"]["today"]["stance"] == STANCE
    assert "positioning_book" in public_rights.rights_status()["account_tools"]
