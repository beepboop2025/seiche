"""The Week Ahead list: optional, off by default, and never near seiche.sqlite.

Four properties are load-bearing and each has a test that fails if it breaks:

  1. the field gates nothing (a free public good has no email wall);
  2. an unset endpoint means the feature is OFF, not erroring;
  3. a Listmonk failure never becomes the reader's failure;
  4. no address can reach `seiche.sqlite`, which holds password hashes.

(4) is asserted structurally, on the module's imports, rather than by watching
one code path stay away from the database. A code path can be added; an import
boundary is checked on every run.
"""

import ast
import logging
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seiche import subscribe


ENDPOINT = "http://127.0.0.1:8797/api/public/subscription"
LIST_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neither dial set unless a test sets it. Without this an operator env
    leaking into the process would make 'off by default' pass by accident."""
    monkeypatch.delenv("SEICHE_LIST_ENDPOINT", raising=False)
    monkeypatch.delenv("SEICHE_LIST_ID", raising=False)


@pytest.fixture()
def client(monkeypatch):
    from seiche import api
    # The limiter is process-global and its window is a rolling minute, so a
    # fresh one per test keeps the rate-limit test from poisoning its
    # neighbours (and vice versa).
    monkeypatch.setattr(api, "_subscribe_limiter",
                        api._RateLimiter(api.SUBSCRIBE_RATE_LIMIT_PER_MIN))
    return TestClient(api.app)


def _on(monkeypatch, endpoint=ENDPOINT, list_id=LIST_UUID):
    monkeypatch.setenv("SEICHE_LIST_ENDPOINT", endpoint)
    monkeypatch.setenv("SEICHE_LIST_ID", list_id)


# ---- off by default ---------------------------------------------------------

def test_feature_is_off_until_both_dials_are_set(monkeypatch):
    assert subscribe.enabled() is False
    monkeypatch.setenv("SEICHE_LIST_ENDPOINT", ENDPOINT)
    assert subscribe.enabled() is False, "an endpoint with no list UUID is not configured"
    monkeypatch.delenv("SEICHE_LIST_ENDPOINT")
    monkeypatch.setenv("SEICHE_LIST_ID", LIST_UUID)
    assert subscribe.enabled() is False, "a list UUID with no endpoint is not configured"
    _on(monkeypatch)
    assert subscribe.enabled() is True


def test_whitespace_only_config_is_off(monkeypatch):
    _on(monkeypatch, endpoint="   ", list_id="  ")
    assert subscribe.enabled() is False


def test_submit_is_a_no_op_when_off(monkeypatch):
    called = []
    monkeypatch.setattr(subscribe.urllib.request, "urlopen",
                        lambda *a, **k: called.append(1))
    assert subscribe.submit("reader@example.com") is False
    assert called == [], "an unconfigured list must not open a socket"


# ---- the GET the front door asks before it draws a form ---------------------

def test_status_says_off_and_hands_back_the_desk(client):
    r = client.get("/api/subscribe")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["mailto"] == "desk@seiche.info"
    assert body["double_opt_in"] is True


def test_status_never_leaks_the_endpoint_or_the_list_uuid(client, monkeypatch):
    _on(monkeypatch)
    body = client.get("/api/subscribe").json()
    assert body["enabled"] is True
    blob = repr(body)
    assert ENDPOINT not in blob and "8797" not in blob
    assert LIST_UUID not in blob


# ---- the POST ---------------------------------------------------------------

def test_post_with_the_list_off_is_not_an_error(client):
    r = client.post("/api/subscribe", json={"email": "reader@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["enabled"] is False and body["delivered"] is False
    assert "desk@seiche.info" in body["message"]


def test_post_hands_listmonk_the_uuid_not_a_numeric_id(client, monkeypatch):
    _on(monkeypatch)
    seen = {}

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = req.data.decode()
        seen["method"] = req.get_method()
        return _Resp()

    monkeypatch.setattr(subscribe.urllib.request, "urlopen", _fake_urlopen)
    r = client.post("/api/subscribe", json={"email": "Reader@Example.COM"})
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert seen["url"] == ENDPOINT and seen["method"] == "POST"
    assert f'"list_uuids": ["{LIST_UUID}"]' in seen["body"]
    # only the domain folds; the local part is not ours to rewrite
    assert '"email": "Reader@example.com"' in seen["body"]


def test_listmonk_failure_never_becomes_the_readers_failure(client, monkeypatch, caplog):
    """The runbook's revert test, in code: with the list pointed somewhere that
    cannot answer, the reader still gets a normal response and the failure shows
    up in the log."""
    _on(monkeypatch)

    def _boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(subscribe.urllib.request, "urlopen", _boom)
    with caplog.at_level(logging.WARNING, logger="seiche.subscribe"):
        r = client.post("/api/subscribe", json={"email": "reader@example.com"})
    assert r.status_code == 200, "a dead newsletter box is not the reader's problem"
    assert r.json()["ok"] is True
    assert r.json()["delivered"] is False, "and we do not claim delivery we did not get"
    assert "desk@seiche.info" in r.json()["message"]
    assert any("listmonk unreachable" in m for m in caplog.messages)


def test_a_500_from_listmonk_is_logged_without_the_address(client, monkeypatch, caplog):
    _on(monkeypatch)

    def _five_hundred(req, timeout=None):
        raise urllib.error.HTTPError(ENDPOINT, 500, "boom", {}, None)

    monkeypatch.setattr(subscribe.urllib.request, "urlopen", _five_hundred)
    with caplog.at_level(logging.INFO, logger="seiche.subscribe"):
        r = client.post("/api/subscribe", json={"email": "private.person@example.com"})
    assert r.status_code == 200 and r.json()["delivered"] is False
    joined = " ".join(caplog.messages)
    assert "private.person@example.com" not in joined, "logs must not spill reader addresses"
    assert "500" in joined


def test_already_subscribed_is_a_fine_outcome(client, monkeypatch):
    _on(monkeypatch)

    def _conflict(req, timeout=None):
        raise urllib.error.HTTPError(ENDPOINT, 409, "already exists", {}, None)

    monkeypatch.setattr(subscribe.urllib.request, "urlopen", _conflict)
    r = client.post("/api/subscribe", json={"email": "reader@example.com"})
    assert r.status_code == 200 and r.json()["delivered"] is True


@pytest.mark.parametrize("bad", ["", "   ", "not-an-address", "no@tld", "@example.com",
                                 "two@@example.com", "a b@example.com", None, 42,
                                 "x" * 250 + "@example.com"])
def test_junk_addresses_are_refused_before_listmonk_is_called(client, monkeypatch, bad):
    _on(monkeypatch)
    monkeypatch.setattr(subscribe.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("junk reached listmonk"))
    assert client.post("/api/subscribe", json={"email": bad}).status_code == 422


def test_a_missing_body_is_refused_not_crashed(client):
    assert client.post("/api/subscribe", json={}).status_code == 422
    assert client.post("/api/subscribe").status_code == 422


def test_the_post_is_rate_limited(client, monkeypatch):
    from seiche import api
    for _ in range(api.SUBSCRIBE_RATE_LIMIT_PER_MIN):
        assert client.post("/api/subscribe", json={"email": "reader@example.com"}).status_code == 200
    r = client.post("/api/subscribe", json={"email": "reader@example.com"})
    assert r.status_code == 429 and r.headers.get("Retry-After")


# ---- it gates nothing -------------------------------------------------------

def test_no_other_route_consults_the_list():
    """The strongest form of "it gates nothing": prove that no handler except
    the two subscribe routes so much as mentions the list module.

    Asserted on the API's AST rather than by calling endpoints, because the
    thing being ruled out is a FUTURE `if subscribed(...)` in some other
    handler, and no amount of calling today's endpoints rules that out."""
    api_src = Path(__file__).resolve().parents[1] / "seiche" / "api.py"
    tree = ast.parse(api_src.read_text())

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in ("subscribe_status", "subscribe_join"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id == "subscribe_list":
                offenders.append(node.name)
                break
    assert not offenders, (
        "Seiche is a free public good and the email field gates nothing, but "
        f"these handlers now read the list: {sorted(set(offenders))}"
    )


def test_subscribe_is_anonymous_even_with_the_board_gate_on(client, monkeypatch):
    """SEICHE_BOARD_AUTH is a setting about the browser board. It must not turn
    the optional email field into a subscriber feature, the same coupling
    mistake 82d5700 removed from the MCP surface."""
    monkeypatch.setenv("SEICHE_BOARD_AUTH", "1")
    assert client.get("/api/subscribe").status_code == 200
    assert client.post("/api/subscribe", json={"email": "reader@example.com"}).status_code == 200


# ---- the boundary that matters ----------------------------------------------

def test_subscribe_module_cannot_reach_the_credential_store():
    """seiche.sqlite holds subscriber password hashes. A marketing list does not
    live in the same file, and the way to keep that true is to make the module
    unable to open it at all: no sqlite3, no seiche.store, no DB_PATH.

    Checked on the AST rather than at runtime, so an import added tomorrow
    fails this test the day it is written and not the day it leaks."""
    tree = ast.parse(Path(subscribe.__file__).read_text())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    assert "sqlite3" not in imported
    assert not {i for i in imported if i.startswith("seiche")}, (
        f"the list module must not import from seiche: {sorted(imported)}"
    )

    # and no identifier that would let it find the database by another route.
    # Names only: the module docstring says "sqlite3" and "seiche.sqlite" on
    # purpose, and a test that forbids naming the risk in prose would push the
    # reasoning out of the file where it belongs.
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    identifiers |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for banned in ("sqlite3", "connect", "DB_PATH", "store", "accounts"):
        assert banned not in identifiers, f"the list module references {banned}"


def test_the_desk_address_is_the_one_fallback():
    """Every off-path ends at a human, and at the same human."""
    assert subscribe.DESK_EMAIL == "desk@seiche.info"
    assert subscribe.status()["mailto"] == subscribe.DESK_EMAIL
    assert subscribe.status()["gates_nothing"] is True
