"""Subscriber accounts: hashing, tokens, and the opt-in Time Machine gate."""

import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
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


def test_duplicate_user_is_insert_only(accounts):
    """Adding a name twice must not become an implicit password reset."""
    accounts.add_user("desk_01", "the original password", tier="founder")
    with pytest.raises(sqlite3.IntegrityError):
        accounts.add_user("desk_01", "a replacement password", tier="pro")
    assert accounts.verify_user("desk_01", "the original password")["tier"] == "founder"
    assert accounts.verify_user("desk_01", "a replacement password") is None


def test_token_verify_expiry_and_tamper(accounts):
    tok = accounts.issue_token("desk_01", "pro")
    ident = accounts.verify_token(tok["token"])
    assert ident == {"username": "desk_01", "tier": "pro"}
    # expired
    old = accounts.issue_token(
        "desk_01", "pro", now=time.time() - accounts.TOKEN_TTL_S - 10
    )
    assert accounts.verify_token(old["token"]) is None
    # tampered tier
    body = tok["token"].split("|")
    body[1] = "founder"
    assert accounts.verify_token("|".join(body)) is None
    malformed = "desk_01|pro|not-an-expiry"
    malformed_sig = hmac.new(
        b"test-secret-not-for-prod", malformed.encode(), hashlib.sha256
    ).hexdigest()
    assert accounts.verify_token(f"{malformed}|{malformed_sig}") is None


def test_current_token_requires_live_account_and_exact_tier(accounts):
    accounts.add_user("live_desk", "correct horse battery", tier="pro")
    valid = accounts.issue_token("live_desk", "pro")["token"]
    assert accounts.verify_current_token(valid) == {
        "username": "live_desk",
        "tier": "pro",
    }

    nonexistent = accounts.issue_token("never_created", "pro")["token"]
    assert accounts.verify_current_token(nonexistent) is None

    accounts.add_user("deleted_desk", "correct horse battery", tier="pro")
    deleted = accounts.issue_token("deleted_desk", "pro")["token"]
    with accounts._conn() as conn:
        conn.execute("DELETE FROM users WHERE username=?", ("deleted_desk",))
    assert accounts.verify_current_token(deleted) is None

    with accounts._conn() as conn:
        conn.execute(
            "UPDATE users SET tier=? WHERE username=?", ("founder", "live_desk")
        )
    assert accounts.verify_current_token(valid) is None
    replacement = accounts.issue_token("live_desk", "founder")["token"]
    assert accounts.verify_current_token(replacement) == {
        "username": "live_desk",
        "tier": "founder",
    }


def test_api_bearer_revokes_deleted_or_retiered_account(accounts):
    from seiche.api import app

    client = TestClient(app)
    accounts.add_user("revocable", "correct horse battery", tier="pro")
    original = accounts.issue_token("revocable", "pro")["token"]
    headers = {"Authorization": f"Bearer {original}"}
    assert client.get("/api/me", headers=headers).status_code == 200

    with accounts._conn() as conn:
        conn.execute(
            "UPDATE users SET tier=? WHERE username=?", ("founder", "revocable")
        )
    assert client.get("/api/me", headers=headers).status_code == 401

    replacement = accounts.issue_token("revocable", "founder")["token"]
    replacement_headers = {"Authorization": f"Bearer {replacement}"}
    assert client.get("/api/me", headers=replacement_headers).status_code == 200

    with accounts._conn() as conn:
        conn.execute("DELETE FROM users WHERE username=?", ("revocable",))
    assert client.get("/api/me", headers=replacement_headers).status_code == 401

    nonexistent = accounts.issue_token("never_created", "pro")["token"]
    assert (
        client.get(
            "/api/me", headers={"Authorization": f"Bearer {nonexistent}"}
        ).status_code
        == 401
    )


def _use_file_secret(accounts, tmp_path, monkeypatch) -> Path:
    data = tmp_path / "auth-data"
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    monkeypatch.delenv("SEICHE_AUTH_SECRET", raising=False)
    monkeypatch.setattr(accounts, "DATA_DIR", data)
    return data


def test_file_auth_secret_is_created_once_and_owner_only(
    accounts, tmp_path, monkeypatch
):
    data = _use_file_secret(accounts, tmp_path, monkeypatch)

    first = accounts._secret()
    second = accounts._secret()

    assert first == second
    assert len(first) == 64
    assert (data / "auth_secret").stat().st_mode & 0o777 == 0o600


def test_file_auth_secret_accepts_owner_read_only_mode(accounts, tmp_path, monkeypatch):
    data = _use_file_secret(accounts, tmp_path, monkeypatch)
    expected = accounts._secret()
    (data / "auth_secret").chmod(0o400)

    assert accounts._secret() == expected


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(0o640, id="group-readable"),
        pytest.param(0o620, id="group-writable"),
        pytest.param(0o604, id="world-readable"),
        pytest.param(0o602, id="world-writable"),
        pytest.param(0o700, id="owner-executable"),
    ],
)
def test_file_auth_secret_rejects_unsafe_mode(accounts, tmp_path, monkeypatch, mode):
    data = _use_file_secret(accounts, tmp_path, monkeypatch)
    accounts._secret()
    (data / "auth_secret").chmod(mode)

    with pytest.raises(ValueError, match="owner-only regular file"):
        accounts._secret()


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "directory"])
def test_file_auth_secret_rejects_unsafe_object(
    accounts, tmp_path, monkeypatch, mutation
):
    data = _use_file_secret(accounts, tmp_path, monkeypatch)
    secret_path = data / "auth_secret"
    accounts._secret()
    if mutation == "symlink":
        outside = tmp_path / "outside-auth-secret"
        outside.write_bytes(secret_path.read_bytes())
        outside.chmod(0o600)
        secret_path.unlink()
        secret_path.symlink_to(outside)
    elif mutation == "hardlink":
        os.link(secret_path, tmp_path / "auth-secret-alias")
    else:
        secret_path.unlink()
        secret_path.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="auth secret"):
        accounts._secret()


def test_file_auth_secret_rejects_wrong_owner(accounts, tmp_path, monkeypatch):
    _use_file_secret(accounts, tmp_path, monkeypatch)
    accounts._secret()
    monkeypatch.setattr(accounts, "_expected_secret_uid", lambda: os.geteuid() + 1)

    with pytest.raises(ValueError, match="owner-only regular file"):
        accounts._secret()


def test_file_auth_secret_rejects_writable_parent(accounts, tmp_path, monkeypatch):
    data = _use_file_secret(accounts, tmp_path, monkeypatch)
    data.chmod(0o770)

    with pytest.raises(ValueError, match="secret directory"):
        accounts._secret()
    assert not (data / "auth_secret").exists()


def test_login_endpoint_and_gate(accounts, monkeypatch):
    from seiche.api import app

    client = TestClient(app)
    accounts.add_user("desk_01", "correct horse battery")

    r = client.post(
        "/api/auth/login", json={"username": "desk_01", "password": "nope nope nope"}
    )
    assert r.status_code == 401
    r = client.post(
        "/api/auth/login",
        json={"username": "desk_01", "password": "correct horse battery"},
    )
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
    assert r.status_code == 422  # authed but bad date — gate passed, validation ran


def test_dispatch_continuation_is_open(accounts, tmp_path, monkeypatch):
    """Seiche is a free public good: the desk's read needs no token."""
    from seiche import api as api_mod
    from seiche.api import app

    # point the dispatch dir at a temp file
    monkeypatch.setattr(api_mod, "DISPATCH_DIR", tmp_path)
    (tmp_path / "test-slug.desk.md").write_text("## the desk read\nfull forward take")
    client = TestClient(app)

    # no token -> full body, open to everyone
    r = client.get("/api/dispatch/test-slug")
    assert r.status_code == 200 and "full forward take" in r.json()["paid"]

    # pre-rename history on the box still serves under the legacy filename
    (tmp_path / "old-slug.paid.md").write_text("## the desk read\nlegacy continuation")
    r = client.get("/api/dispatch/old-slug")
    assert r.status_code == 200 and "legacy continuation" in r.json()["paid"]

    # Current naming wins deterministically when both generations exist.
    (tmp_path / "same-slug.paid.md").write_text("legacy body")
    (tmp_path / "same-slug.desk.md").write_text("current body")
    assert client.get("/api/dispatch/same-slug").json()["paid"] == "current body"

    # Enumerating trusted regular files keeps a valid request slug from
    # selecting a symlink or an oversized automated-publishing artifact.
    outside = tmp_path.parent / "outside-dispatch.md"
    outside.write_text("must never be served")
    (tmp_path / "escaped-slug.desk.md").symlink_to(outside)
    assert client.get("/api/dispatch/escaped-slug").status_code == 404
    (tmp_path / "huge-slug.desk.md").write_bytes(
        b"x" * (api_mod._DISPATCH_MAX_BYTES + 1)
    )
    assert client.get("/api/dispatch/huge-slug").status_code == 503

    # bad slugs still rejected, missing continuations still 404
    assert client.get("/api/dispatch/NOT%20a%20slug").status_code in (404, 422)
    assert client.get("/api/dispatch/absent-slug").status_code == 404


def test_board_gated_public_free(accounts, monkeypatch, fake_snap):
    from seiche import api as api_mod
    from seiche.api import app

    async def cached_snapshot(force=False):
        return fake_snap

    monkeypatch.setattr(api_mod.assemble, "snapshot", cached_snapshot)
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
    tok = client.post(
        "/api/auth/login",
        json={"username": "desk_01", "password": "correct horse battery"},
    ).json()["token"]
    assert (
        client.get(
            "/api/overview", headers={"Authorization": f"Bearer {tok}"}
        ).status_code
        == 200
    )


def test_alert_prefs_gated_and_persist(accounts, monkeypatch):
    from seiche.api import app

    client = TestClient(app)
    accounts.add_user("desk_01", "correct horse battery")
    tok = client.post(
        "/api/auth/login",
        json={"username": "desk_01", "password": "correct horse battery"},
    ).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}

    assert client.get("/api/alerts/prefs").status_code == 401  # gated
    assert client.get("/api/alerts/prefs", headers=H).json() == {
        "email": "",
        "alerts_on": False,
    }

    # can't enable without an email
    assert (
        client.post(
            "/api/alerts/prefs", json={"email": "", "alerts_on": True}, headers=H
        ).status_code
        == 422
    )
    # set + read back
    r = client.post(
        "/api/alerts/prefs", json={"email": "d@x.com", "alerts_on": True}, headers=H
    )
    assert r.status_code == 200 and r.json() == {"email": "d@x.com", "alerts_on": True}
    assert accounts.alert_recipients() == ["d@x.com"]
    # turning off removes from fan-out
    client.post(
        "/api/alerts/prefs", json={"email": "d@x.com", "alerts_on": False}, headers=H
    )
    assert accounts.alert_recipients() == []


def test_mailer_unconfigured_is_noop(monkeypatch):
    from seiche import mailer

    for k in ("SEICHE_SMTP_HOST", "SEICHE_SMTP_USER", "SEICHE_SMTP_PASS"):
        monkeypatch.delenv(k, raising=False)
    assert mailer.configured() is False
    assert mailer.send("a@b.com", "s", "b") is False  # never raises, returns False


def test_json_safe_nulls_non_finite_floats():
    """Historical replays can carry NaN/Inf; strict JSON must still serialize."""
    from seiche.api import _json_safe

    dirty = {
        "a": float("nan"),
        "b": [1.0, float("inf"), {"c": float("-inf"), "d": "text"}],
        "e": 2,
    }
    clean = _json_safe(dirty)
    assert clean == {"a": None, "b": [1.0, None, {"c": None, "d": "text"}], "e": 2}
    import json

    json.dumps(clean)  # must not raise
