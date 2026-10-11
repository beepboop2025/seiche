import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import pwd

import pytest


spec = importlib.util.spec_from_file_location("renew_undertow_token", Path(__file__).parents[1] / "ops" / "renew_undertow_token.py")
renewal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renewal)


class FakeNative:
    """Synthetic issuer with the native public function signatures."""
    def __init__(self):
        self.issued = 0
        self.permitted = True
        self.verify_times = []

    def entitlement(self, chat_id, state, *, owner=None):
        return {"entitled": self.permitted, "grants": ["mcp"] if self.permitted else []}

    def issue_agent_token(self, chat_id, name, secret, ttl_s=2592000, now=None):
        self.issued += 1
        body = json.dumps({"sub": chat_id, "name": name, "aud": "mcp", "exp": now + ttl_s}).encode()
        part = base64.urlsafe_b64encode(body).decode().rstrip("=")
        return part + "." + hmac.new(secret.encode(), part.encode(), hashlib.sha256).hexdigest()

    def verify_agent_token(self, token, secret, now=None):
        self.verify_times.append(now)
        try:
            part, signature = token.split(".")
            if not hmac.compare_digest(signature, hmac.new(secret.encode(), part.encode(), hashlib.sha256).hexdigest()):
                return None
            body = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            if body["aud"] != "mcp" or now >= body["exp"]:
                return None
            return {"chat_id": body["sub"], "name": body["name"], "exp": body["exp"]}
        except (ValueError, KeyError):
            return None


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    repo = tmp_path / "native"
    repo.mkdir()
    state = tmp_path / "entitlement-state"
    state.mkdir()
    env = tmp_path / "native.env"
    env.write_text(f"UNDERTOW_WEB_SECRET='synthetic-signing-secret'\nUNDERTOW_OWNER_CHAT='synthetic-owner'\nUNDERTOW_BOT_STATE={state}\nUNDERTOW_REPO={repo}\n")
    env.chmod(0o600)
    output = tmp_path / "undertow-token.json"
    native = FakeNative()
    user = pwd.getpwuid(os.getuid()).pw_name
    def run(now=1800000000):
        return renewal.renew(env, repo, output, user, now=now, native=native)
    return dict(root=tmp_path, repo=repo, env=env, output=output, native=native, run=run)


def test_create_current_and_atomic_thirty_day_rotation(setup):
    result = setup["run"]()
    assert result["status"] == "created" and result["entitlement_changed"] is False
    original = setup["output"].read_bytes()
    original_inode = setup["output"].stat().st_ino
    record = json.loads(original)
    assert set(record) == renewal.ENVELOPE_KEYS
    assert datetime.fromisoformat(record["expires_at"]).timestamp() == 1800000000 + renewal.TTL_SECONDS
    assert setup["output"].stat().st_mode & 0o777 == 0o600
    assert setup["output"].stat().st_uid == os.getuid()
    assert setup["run"](1800000000 + 22 * 86400)["status"] == "current"
    assert setup["output"].read_bytes() == original
    assert setup["native"].issued == 1
    rotated = setup["run"](1800000000 + 23 * 86400)
    assert rotated["status"] == "renewed"
    assert setup["output"].read_bytes() != original
    assert setup["output"].stat().st_ino != original_inode
    assert setup["native"].issued == 2
    assert not [path for path in setup["root"].glob(".undertow-token-*") if path.name != ".undertow-token-renew.lock"]


def test_valid_expired_token_can_renew_but_invalid_signature_cannot(setup):
    setup["run"]()
    assert setup["run"](1800000000 + 31 * 86400)["status"] == "renewed"
    original = setup["output"].read_bytes()
    setup["env"].write_text(setup["env"].read_text().replace("synthetic-signing-secret", "changed-signing-secret"))
    with pytest.raises(ValueError):
        setup["run"](1800000000 + 59 * 86400)
    assert setup["output"].read_bytes() == original
    assert setup["native"].issued == 2
    assert 0 in setup["native"].verify_times


@pytest.mark.parametrize("mutation", ["expiry", "identity", "scope", "extra_key"])
def test_rejects_mismatched_existing_envelopes_without_overwrite(setup, mutation):
    setup["run"]()
    record = json.loads(setup["output"].read_text())
    if mutation == "expiry":
        record["expires_at"] = "2040-01-01T00:00:00+00:00"
    elif mutation == "identity":
        record["token"] = setup["native"].issue_agent_token("another-synthetic-owner", "test", "synthetic-signing-secret", now=1800000000)
    elif mutation == "scope":
        record["purpose"] = "another-purpose"
    else:
        record["unrecognized"] = True
    setup["output"].write_text(json.dumps(record))
    original = setup["output"].read_bytes()
    with pytest.raises(ValueError):
        setup["run"]()
    assert setup["output"].read_bytes() == original


def test_literal_environment_never_executes_shell_and_duplicate_fails(setup):
    marker = setup["root"] / "must-not-exist"
    content = setup["env"].read_text().replace("synthetic-signing-secret", "$(touch " + str(marker) + ")")
    setup["env"].write_text(content + "IGNORED_COMMAND=$(touch " + str(marker) + ")\n")
    setup["run"]()
    assert not marker.exists()
    setup["env"].write_text(content + "UNDERTOW_WEB_SECRET=duplicate\n")
    with pytest.raises(ValueError):
        setup["run"]()


def test_private_files_symlinks_and_entitlement_fail_closed(setup):
    setup["native"].permitted = False
    with pytest.raises(ValueError):
        setup["run"]()
    assert not setup["output"].exists()
    setup["native"].permitted = True
    setup["env"].chmod(0o644)
    with pytest.raises(ValueError):
        setup["run"]()
    setup["env"].chmod(0o600)
    target = setup["root"] / "unrelated"
    target.write_text("preserve")
    target.chmod(0o600)
    setup["output"].symlink_to(target)
    with pytest.raises(OSError):
        setup["run"]()
    assert target.read_text() == "preserve"


def test_cli_errors_never_disclose_native_secret_or_identity(setup, monkeypatch, capsys):
    monkeypatch.setattr(renewal.os, "geteuid", lambda: 0)
    def failure(*args, **kwargs):
        raise ValueError("synthetic-signing-secret synthetic-owner")
    monkeypatch.setattr(renewal, "renew", failure)
    assert renewal.main(["--native-env", str(setup["env"]), "--output", str(setup["output"])]) == 1
    text = capsys.readouterr()
    assert "synthetic-signing-secret" not in text.out + text.err
    assert "synthetic-owner" not in text.out + text.err
    assert "renewal failed" in text.err


def test_overlapping_run_is_refused_and_failed_issuer_preserves_existing(setup, monkeypatch):
    setup["run"]()
    original = setup["output"].read_bytes()
    with (setup["root"] / ".undertow-token-renew.lock").open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            setup["run"](1800000000 + 24 * 86400)
    assert setup["output"].read_bytes() == original
    monkeypatch.setattr(setup["native"], "issue_agent_token", lambda *args, **kwargs: "invalid-token")
    with pytest.raises(ValueError):
        setup["run"](1800000000 + 24 * 86400)
    assert setup["output"].read_bytes() == original


def test_root_unit_has_no_native_env_export_and_timer_is_daily():
    ops = Path(__file__).parents[1] / "ops"
    service = (ops / "seiche-agent-upstream-token.service").read_text()
    assert "User=root" in service and "--runtime-user seiche-network" in service
    assert "EnvironmentFile=" not in service
    assert "SuccessExitStatus=" not in service
    assert "RestrictAddressFamilies=AF_UNIX" in service
    timer = (ops / "seiche-agent-upstream-token.timer").read_text()
    assert "OnCalendar=*-*-* 04:00:00 UTC" in timer
    assert "Persistent=true" in timer
