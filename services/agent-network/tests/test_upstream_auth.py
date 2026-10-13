import json
import os

import httpx
import pytest

from seiche_agent_network.upstream_auth import (
    ProbeAuth, TOKEN_FILE_ENV, UNDERTOW_URL, UpstreamAuthUnavailable,
    load_undertow_credential, public_call_credential,
)


def test_private_credential_reads_expiry_and_atomic_rotation(upstream_credential):
    token, path = upstream_credential(now=1000)
    value = load_undertow_credential(now=1000)
    assert value.status == "ready" and value.token == token
    assert token not in repr(value) and token not in json.dumps(value.metadata())
    assert load_undertow_credential(now=4600).status == "expired"
    renewed, _ = upstream_credential(now=1100, name="renewed")
    replacement = path.with_suffix(".replacement")
    path.rename(replacement)
    os.replace(replacement, path)
    assert load_undertow_credential(now=1100).token == renewed != token


@pytest.mark.parametrize("mutation", ["permissions", "symlink", "oversized", "expiry_mismatch", "unknown_field", "wrong_purpose"])
def test_unsafe_private_credential_fails_closed(upstream_credential, mutation):
    _, path = upstream_credential(now=1000)
    if mutation == "permissions":
        path.chmod(0o644)
    elif mutation == "symlink":
        real = path.with_suffix(".real")
        path.rename(real)
        path.symlink_to(real)
    elif mutation == "oversized":
        path.write_text("x" * 16385)
    else:
        data = json.loads(path.read_text())
        if mutation == "expiry_mismatch": data["expires_at"] = "2026-10-11T00:00:00Z"
        if mutation == "unknown_field": data["private_owner"] = "never expose"
        if mutation == "wrong_purpose": data["purpose"] = "subscriber_access"
        path.write_text(json.dumps(data))
    assert load_undertow_credential(now=1000).status in {"invalid", "unreadable"}
    with pytest.raises(UpstreamAuthUnavailable):
        public_call_credential("undertow", "tools/call", "exit_cost", now=1000)


def test_expired_audience_or_future_token_never_falls_back(upstream_credential, monkeypatch):
    for changes in [{"aud": "web"}, {"iat": 2000}, {"exp": 1000}]:
        upstream_credential(now=1000, changes=changes)
        with pytest.raises(UpstreamAuthUnavailable):
            public_call_credential("undertow", "tools/call", "exit_cost", now=1000)
    monkeypatch.delenv(TOKEN_FILE_ENV)
    assert load_undertow_credential().status == "not_configured"
    assert public_call_credential("undertow", "tools/call", "exit_cost") is None


def test_credentials_only_for_fixed_public_tools(upstream_credential):
    token, _ = upstream_credential(now=1000)
    assert public_call_credential("undertow", "tools/call", "exit_cost", now=1000) == token
    for product, method, name in [("seiche", "tools/call", "exit_cost"), ("undertow", "tools/list", None),
                                 ("undertow", "initialize", None), ("undertow", "tools/call", "agent_access_status")]:
        assert public_call_credential(product, method, name, now=1000) is None
    for name in ["board_full", "exit_desk_full", "unregistered_tool", ["exit_cost"], None]:
        with pytest.raises(UpstreamAuthUnavailable):
            public_call_credential("undertow", "tools/call", name, now=1000)


async def test_probe_hooks_strip_identity_and_never_retain_session(upstream_credential):
    token, _ = upstream_credential(now=1000)
    auth = ProbeAuth(clock=lambda: 1000)
    seen = []
    async def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"Set-Cookie": "owner=session; Path=/"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream),
            event_hooks={"request": [auth.request], "response": [auth.response]}) as client:
        await client.post(UNDERTOW_URL, headers={"Authorization": "Bearer visitor", "Mcp-Session-Id": "untrusted"},
                          json={"method": "tools/call", "params": {"name": "exit_cost"}})
        await client.post(UNDERTOW_URL, json={"method": "tools/list"})
        await client.post(UNDERTOW_URL, json={"method": "tools/call", "params": {"name": "agent_access_status"}})
        await client.post("https://elsewhere.invalid/mcp", headers={"Authorization": "Bearer visitor"}, json={"method": "tools/list"})
    assert seen[0].headers["authorization"] == "Bearer " + token
    assert all("authorization" not in item.headers for item in seen[1:])
    assert all("cookie" not in item.headers and "mcp-session-id" not in item.headers for item in seen)
    response = httpx.Response(200, request=seen[0], headers={"Mcp-Session-Id": "privileged"})
    with pytest.raises(UpstreamAuthUnavailable): await auth.response(response)
    assert response.is_closed
