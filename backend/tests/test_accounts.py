"""Subscriber accounts: hashing, tokens, and the opt-in Time Machine gate."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    from seiche import accounts as acc
    monkeypatch.setattr(acc, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    return acc


def test_password_roundtrip_and_rejects(accounts):
    accounts.add_user("desk_01", "correct horse battery", tier="pro")
    assert accounts.verify_user("desk_01", "correct horse battery")["tier"] == "pro"
    assert accounts.verify_user("desk_01", "wrong password!") is None
    assert accounts.verify_user("nobody", "correct horse battery") is None


def test_weak_password_and_bad_username_refused(accounts):
    with pytest.raises(ValueError):
        accounts.add_user("desk", "short")
    with pytest.raises(ValueError):
        accounts.add_user("evil name;--", "long enough password")


def test_token_verify_expiry_and_tamper(accounts):
    tok = accounts.issue_token("desk_01", "pro")
    ident = accounts.verify_token(tok["token"])
    assert ident == {"username": "desk_01", "tier": "pro"}
    # expired
    old = accounts.issue_token("desk_01", "pro", now=time.time() - accounts.TOKEN_TTL_S - 10)
    assert accounts.verify_token(old["token"]) is None
    # tampered tier
    body = tok["token"].split("|")
    body[1] = "founder"
    assert accounts.verify_token("|".join(body)) is None


def test_login_endpoint_and_gate(accounts, monkeypatch):
    from seiche.api import app
    client = TestClient(app)
    accounts.add_user("desk_01", "correct horse battery")

    r = client.post("/api/auth/login", json={"username": "desk_01", "password": "nope nope nope"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "desk_01", "password": "correct horse battery"})
    assert r.status_code == 200
    token = r.json()["token"]

    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["username"] == "desk_01"
    assert client.get("/api/me").status_code == 401

    # the gate is opt-in: off by default, 401 without a token when on
    monkeypatch.setenv("SEICHE_ASOF_AUTH", "1")
    r = client.get("/api/asof/2026-07-01")
    assert r.status_code == 401
    r = client.get("/api/asof/not-a-date", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 422   # authed but bad date — gate passed, validation ran


def test_dispatch_paid_is_token_gated(accounts, tmp_path, monkeypatch):
    from seiche import api as api_mod
    from seiche.api import app
    # point the dispatch dir at a temp file
    monkeypatch.setattr(api_mod, "DISPATCH_DIR", tmp_path)
    (tmp_path / "test-slug.paid.md").write_text("## the paid read\nsecret desk take")
    client = TestClient(app)
    accounts.add_user("desk_01", "correct horse battery")

    # no token -> 401, no leak
    r = client.get("/api/dispatch/test-slug")
    assert r.status_code == 401
    assert "secret desk take" not in r.text

    # valid token -> the paid body
    tok = client.post("/api/auth/login", json={"username": "desk_01", "password": "correct horse battery"}).json()["token"]
    r = client.get("/api/dispatch/test-slug", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and "secret desk take" in r.json()["paid"]

    # bad slug rejected
    assert client.get("/api/dispatch/../etc/passwd", headers={"Authorization": f"Bearer {tok}"}).status_code in (404, 422)


def test_board_gated_public_free(accounts, monkeypatch):
    from seiche.api import app
    client = TestClient(app)
    accounts.add_user("desk_01", "correct horse battery")

    # /api/public is always free and carries no gated board data
    r = client.get("/api/public")
    assert r.status_code == 200
    body = r.json()
    assert "conclusion" in body and "proof" in body
    assert "engines" not in body and "navigator" not in body  # board not leaked

    # with the gate on, the full board needs a token
    monkeypatch.setenv("SEICHE_BOARD_AUTH", "1")
    assert client.get("/api/overview").status_code == 401
    tok = client.post("/api/auth/login", json={"username": "desk_01", "password": "correct horse battery"}).json()["token"]
    assert client.get("/api/overview", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_alert_prefs_gated_and_persist(accounts, monkeypatch):
    from seiche.api import app
    client = TestClient(app)
    accounts.add_user("desk_01", "correct horse battery")
    tok = client.post("/api/auth/login", json={"username": "desk_01", "password": "correct horse battery"}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}

    assert client.get("/api/alerts/prefs").status_code == 401  # gated
    assert client.get("/api/alerts/prefs", headers=H).json() == {"email": "", "alerts_on": False}

    # can't enable without an email
    assert client.post("/api/alerts/prefs", json={"email": "", "alerts_on": True}, headers=H).status_code == 422
    # set + read back
    r = client.post("/api/alerts/prefs", json={"email": "d@x.com", "alerts_on": True}, headers=H)
    assert r.status_code == 200 and r.json() == {"email": "d@x.com", "alerts_on": True}
    assert accounts.alert_recipients() == ["d@x.com"]
    # turning off removes from fan-out
    client.post("/api/alerts/prefs", json={"email": "d@x.com", "alerts_on": False}, headers=H)
    assert accounts.alert_recipients() == []


def test_mailer_unconfigured_is_noop(monkeypatch):
    from seiche import mailer
    for k in ("SEICHE_SMTP_HOST", "SEICHE_SMTP_USER", "SEICHE_SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    assert mailer.configured() is False
    assert mailer.send("a@b.com", "s", "b") is False  # never raises, returns False
