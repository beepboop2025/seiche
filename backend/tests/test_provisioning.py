"""Provisioning: payment -> account + token, idempotency, and the signed
webhook. No SMTP and no network — credential delivery is best-effort and
skipped when unconfigured.
"""

import hashlib
import hmac
import json

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
