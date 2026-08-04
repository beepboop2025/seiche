"""Provisioning: payment -> account + token, idempotency, and the signed
webhook. No SMTP and no network — credential delivery is best-effort and
skipped when unconfigured.
"""

import hashlib
import hmac
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def prov(tmp_path, monkeypatch):
    from seiche import accounts, provisioning
    db = tmp_path / "t.sqlite"
    monkeypatch.setattr(provisioning, "DB_PATH", db)
    monkeypatch.setattr(accounts, "DB_PATH", db)
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    return provisioning


# ---- core -------------------------------------------------------------------

def test_provision_creates_working_account_and_token(prov):
    from seiche import accounts
    r = prov.provision("pro", email="alice@example.com", payment_ref="tx1")
    assert r["already"] is False and r["tier"] == "pro"
    assert accounts.verify_user(r["username"], r["password"])["tier"] == "pro"
    assert accounts.verify_token(r["token"])["username"] == r["username"]


def test_idempotent_on_payment_ref(prov):
    first = prov.provision("pro", payment_ref="dup")
    second = prov.provision("pro", payment_ref="dup")
    assert second["already"] is True
    assert second["username"] == first["username"]
    assert second["password"] is None and second["token"] is None


def test_unknown_tier_refused(prov):
    with pytest.raises(prov.ProvisionError):
        prov.provision("platinum", payment_ref="x")


def test_username_derived_from_email(prov):
    r = prov.provision("founder", email="bob.smith@corp.com", payment_ref="e1")
    assert r["username"].startswith("bobsmith_")   # sanitised local-part + suffix


def test_missing_ref_still_records_and_is_unique(prov):
    a = prov.provision("pro")
    b = prov.provision("pro")
    assert a["username"] != b["username"]          # distinct synthetic refs


def test_provision_never_overwrites_existing_account(prov):
    """A colliding username (e.g. a buyer-supplied one echoed via the webhook)
    must NOT clobber an existing account's credentials — the payer gets a fresh
    suffixed account instead, and the victim's login still works."""
    from seiche import accounts
    accounts.add_user("mrinal", "the founders own password", tier="founder")
    r = prov.provision("pro", username="mrinal", payment_ref="attack")
    assert r["username"] != "mrinal"               # granted a different name
    assert r["username"].startswith("mrinal_")
    # the original account is untouched
    assert accounts.verify_user("mrinal", "the founders own password")["tier"] == "founder"
    assert accounts.verify_user("mrinal", r["password"]) is None


def test_account_failure_rolls_back_payment_and_retry_succeeds(prov, monkeypatch):
    """A paid reference is retryable when account creation fails mid-grant."""
    from seiche import accounts

    real_add = accounts.add_user
    monkeypatch.setattr(
        accounts,
        "add_user",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with pytest.raises(RuntimeError, match="disk full"):
        prov.provision("pro", username="retry_me", payment_ref="retry-ref")

    with sqlite3.connect(prov.DB_PATH) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM provisions WHERE payment_ref='retry-ref'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE username='retry_me'"
        ).fetchone()[0] == 0

    monkeypatch.setattr(accounts, "add_user", real_add)
    retried = prov.provision("pro", username="retry_me", payment_ref="retry-ref")
    assert retried["already"] is False
    assert accounts.verify_user("retry_me", retried["password"])["tier"] == "pro"


def _run_together(*calls):
    barrier = threading.Barrier(len(calls))

    def start(call):
        barrier.wait(timeout=5)
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(start, calls))


def test_concurrent_duplicate_payment_mints_one_account(prov):
    results = _run_together(
        lambda: prov.provision("pro", username="same_buyer", payment_ref="same-ref"),
        lambda: prov.provision("pro", username="same_buyer", payment_ref="same-ref"),
    )
    assert sorted(r["already"] for r in results) == [False, True]
    assert {r["username"] for r in results} == {"same_buyer"}
    with sqlite3.connect(prov.DB_PATH) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM provisions WHERE payment_ref='same-ref'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE username='same_buyer'"
        ).fetchone()[0] == 1


def test_concurrent_username_collision_suffixes_second_grant(prov):
    results = _run_together(
        lambda: prov.provision("pro", username="shared", payment_ref="first-ref"),
        lambda: prov.provision("pro", username="shared", payment_ref="second-ref"),
    )
    names = {r["username"] for r in results}
    assert len(names) == 2
    assert "shared" in names
    assert any(name.startswith("shared_") for name in names - {"shared"})
    with sqlite3.connect(prov.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM provisions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


# ---- signature & gate -------------------------------------------------------

def test_signature_roundtrip(prov, monkeypatch):
    monkeypatch.setenv("SEICHE_PROVISION_SECRET", "topsecret")
    body = b'{"tier":"pro"}'
    sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    assert prov.verify_signature(body, sig)
    assert prov.verify_signature(body, "sha256=" + sig)   # prefixed form
    assert not prov.verify_signature(body, "deadbeef")
    assert not prov.verify_signature(body, None)


def test_enabled_reflects_secret(prov, monkeypatch):
    monkeypatch.delenv("SEICHE_PROVISION_SECRET", raising=False)
    assert prov.enabled() is False
    monkeypatch.setenv("SEICHE_PROVISION_SECRET", "x")
    assert prov.enabled() is True


# ---- HTTP webhook -----------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from seiche import accounts, api, provisioning
    db = tmp_path / "t.sqlite"
    monkeypatch.setattr(provisioning, "DB_PATH", db)
    monkeypatch.setattr(accounts, "DB_PATH", db)
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    return TestClient(api.app)


def _signed(client, secret, obj):
    body = json.dumps(obj).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/provision", content=body,
                       headers={"X-Seiche-Signature": sig,
                                "content-type": "application/json"})


def test_webhook_disabled_without_secret(client, monkeypatch):
    monkeypatch.delenv("SEICHE_PROVISION_SECRET", raising=False)
    assert client.post("/api/provision", content=b"{}").status_code == 503


def test_webhook_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("SEICHE_PROVISION_SECRET", "s3cr3t")
    r = client.post("/api/provision", content=b'{"tier":"pro"}',
                    headers={"X-Seiche-Signature": "nope"})
    assert r.status_code == 401


def test_webhook_provisions_on_valid_signature(client, monkeypatch):
    monkeypatch.setenv("SEICHE_PROVISION_SECRET", "s3cr3t")
    r = _signed(client, "s3cr3t", {"tier": "pro", "email": "c@x.com", "payment_ref": "inv_9"})
    assert r.status_code == 200
    j = r.json()
    assert j["tier"] == "pro" and j["password"] and j["token"]


def test_webhook_idempotent_replay(client, monkeypatch):
    monkeypatch.setenv("SEICHE_PROVISION_SECRET", "s3cr3t")
    _signed(client, "s3cr3t", {"tier": "pro", "payment_ref": "inv_same"})
    again = _signed(client, "s3cr3t", {"tier": "pro", "payment_ref": "inv_same"})
    assert again.json()["already"] is True


def test_webhook_bad_tier_is_422(client, monkeypatch):
    monkeypatch.setenv("SEICHE_PROVISION_SECRET", "s3cr3t")
    r = _signed(client, "s3cr3t", {"tier": "gold", "payment_ref": "x"})
    assert r.status_code == 422
