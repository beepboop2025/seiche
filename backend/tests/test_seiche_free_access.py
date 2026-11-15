"""Seiche is free forever: no paid tier, no upgrade path and no payment hook.

This guard fails if any of them comes back:
- a daily quota keyed by a commercial tier, a tier list a payment can buy into,
  or an upgrade URL constant;
- a provisioning or payment route on the application, or the provisioning
  module or ``seiche provision`` command;
- an upgrade URL in the MCP usage report;
- sign-in errors or copy that call a free sign-in a subscriber feature.

What it keeps: anonymous callers get 200 tool calls per UTC day per IP, free
signed-in accounts 5000, operator accounts are uncapped, and stored values from
before (pro, founder, enterprise) keep working with no data migration.

Stated limits of this guard: the dormant x402 pay-per-call extension on /mcp
(``seiche/x402.py``) awaits the owner's ruling and is not checked here, and the
edge allow-list in ``ops/Caddyfile`` (which still names ``/api/provision``) is a
release-owner change, not application code.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from seiche import accounts, api, cli, config, usage

ROOT = Path(__file__).resolve().parents[2]
PAYMENT_PATH = re.compile(
    r"provision|payment|checkout|billing|invoice|purchase|pricing|upgrade|stripe|btcpay|nowpayments",
    re.IGNORECASE,
)
PAYWALL_WORDING = re.compile(
    r"subscriber[ -]feature|subscriber access|subscriber tier|paid subscription|"
    r"upgrade for a higher limit|commercial dial|per-tier",
    re.IGNORECASE,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite")
    monkeypatch.setattr(accounts, "DB_PATH", tmp_path / "accounts.sqlite")
    monkeypatch.setenv("SEICHE_AUTH_SECRET", "test-secret-not-for-prod")
    monkeypatch.delenv(config.OPERATOR_USERS_ENV, raising=False)
    return TestClient(api.app)


def test_no_paid_tier_configuration_remains():
    for name in ("MCP_DAILY_QUOTAS", "SUBSCRIBER_TIERS", "MCP_UPGRADE_URL"):
        assert not hasattr(config, name), f"config.{name} is back"
    assert set(config.LEGACY_ACCESS.values()) <= {config.ACCESS_FREE, config.ACCESS_OPERATOR}
    assert config.MCP_ANON_DAILY == 200
    assert config.MCP_RATE_LIMIT_PER_MIN == 60


@pytest.mark.parametrize(
    ("stored", "level", "quota"),
    [
        ("free", "free", 5000),
        ("operator", "operator", None),
        # written before 0.16.15: no migration, the same allowance as before
        ("pro", "free", 5000),
        ("founder", "operator", None),
        ("enterprise", "operator", None),
        (" Founder ", "operator", None),
        # any other stored value is still a signed-in, free account
        ("trial", "free", 5000),
        ("", "free", 5000),
    ],
)
def test_every_stored_value_reads_as_free_or_operator(stored, level, quota, monkeypatch):
    monkeypatch.delenv(config.OPERATOR_USERS_ENV, raising=False)
    identity = {"username": "desk_01", "tier": stored}
    assert accounts.access_level(identity) == level
    assert usage.quota_for(identity) == quota


def test_anonymous_allowance_and_operator_allowlist(monkeypatch):
    assert accounts.access_level(None) == "anon"
    assert usage.quota_for(None) == 200
    monkeypatch.setenv(config.OPERATOR_USERS_ENV, " owner_1 , monitor_1 ,")
    assert accounts.access_level({"username": "monitor_1", "tier": "pro"}) == "operator"
    assert usage.quota_for({"username": "monitor_1", "tier": "pro"}) is None
    assert accounts.access_level({"username": "desk_01", "tier": "pro"}) == "free"


def test_new_accounts_store_free_or_operator_only():
    assert accounts.canonical_access(None) == "free"
    assert accounts.canonical_access("pro") == "free"
    assert accounts.canonical_access("founder") == "operator"
    assert accounts.canonical_access("Operator") == "operator"
    with pytest.raises(ValueError):
        accounts.canonical_access("enterprise_plus")


def test_legacy_tokens_keep_working_without_a_migration(client):
    accounts.add_user("legacy_pro", "correct horse battery", tier="pro")
    accounts.add_user("legacy_founder", "correct horse battery", tier="founder")
    for username, stored, level, limit in (
        ("legacy_pro", "pro", "free", 5000),
        ("legacy_founder", "founder", "operator", None),
    ):
        token = accounts.issue_token(username, stored)["token"]
        assert accounts.verify_current_token(token) == {"username": username, "tier": stored}
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/me", headers=headers).json()
        assert me == {"username": username, "tier": level}
        report = client.get("/mcp/usage", headers=headers).json()
        assert report["tier"] == level and report["daily_limit"] == limit


def test_no_provisioning_or_payment_route_or_module():
    paths = sorted({getattr(route, "path", "") for route in api.app.routes})
    assert paths, "application routes found"
    assert [path for path in paths if PAYMENT_PATH.search(path)] == []
    assert importlib.util.find_spec("seiche.provisioning") is None


def test_cli_has_no_provision_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["seiche", "provision", "--tier", "pro"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 2
    assert "invalid choice: 'provision'" in capsys.readouterr().err


def test_usage_report_offers_no_upgrade(client):
    report = client.get("/mcp/usage").json()
    assert report["tier"] == "anon"
    assert report["daily_limit"] == 200
    # the key stays for older clients; there is nothing to upgrade to
    assert "upgrade_url" in report and report["upgrade_url"] is None


def test_sign_in_gates_do_not_imply_payment(client, monkeypatch):
    monkeypatch.setenv("SEICHE_BOARD_AUTH", "1")
    board = client.get("/api/deep")
    assert board.status_code == 401
    assert board.json()["detail"] == "sign in to use this endpoint"
    monkeypatch.delenv("SEICHE_BOARD_AUTH")
    monkeypatch.setenv("SEICHE_ASOF_AUTH", "1")
    # Time Machine replay is withheld in this release: the legacy scope answers
    # /api/asof with 403 before the handler, so its own gate is checked directly.
    with pytest.raises(HTTPException) as replay:
        asyncio.run(api.asof("2026-01-02", Response(), ident=None))
    assert replay.value.status_code == 401
    assert replay.value.detail == "sign in to use Time Machine replay"
    for detail in (board.json()["detail"], replay.value.detail):
        assert not re.search("[\u2013\u2014]", detail)


def test_no_paywall_wording_in_backend_frontend_or_mcp_docs():
    files = [
        *sorted((ROOT / "backend" / "seiche").rglob("*.py")),
        *sorted((ROOT / "frontend" / "src").rglob("*.ts")),
        *sorted((ROOT / "frontend" / "src").rglob("*.tsx")),
        ROOT / "docs" / "MCP.md",
    ]
    assert len(files) > 100
    found = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PAYWALL_WORDING.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}")
    assert found == []
