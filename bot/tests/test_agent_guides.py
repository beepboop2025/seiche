"""Guide entry points must not enroll visitors or touch private/market state."""
import importlib.util
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['seiche']

@pytest.fixture(params=NAMES)
def bot(request):
    path = ROOT / 'bot' / (request.param + "_bot.py")
    spec = importlib.util.spec_from_file_location("guide_" + request.param, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return request.param, module


def invoke(module, command, chat_type="private", callback=False):
    if hasattr(module, "handle_update"):
        message = {"text": command, "chat": {"id": 42, "type": chat_type}, "from": {"id": 42}}
        update = {"callback_query": {"data": command, "message": message, "from": {"id": 42}}} if callback else {"message": message}
        module.handle_update(update)
    else:
        module.handle(42, command, chat_type=chat_type)


@pytest.mark.parametrize("command", ["/agents", "/updates", "/data", "/start tool_agents", "/start tool_updates", "/start tool_data"])
def test_discovery_needs_no_state_or_market_access(bot, monkeypatch, command):
    name, module = bot
    for method in ("load_state", "save_state", "load_set", "save_set", "load_paypal_set", "record_lead", "_safe_record_activation", "_record_activation", "_record_first_open", "api_get", "mcp_call", "load_snapshot", "load_crypto_desk", "_fetch_board"):
        if hasattr(module, method):
            monkeypatch.setattr(module, method, lambda *a, **kw: pytest.fail("guide touched state or market data"))
    sent = []
    monkeypatch.setattr(module, "send", lambda *a, **kw: sent.append(a))
    invoke(module, command)
    assert len(sent) == 1
    assert "https://agents.seiche.info/" in sent[0][1]
    assert len(sent[0][1]) < 4096
    assert any(button.get("url") == "https://agents.seiche.info/connect" for row in sent[0][2] for button in row)
    if command.endswith("data"):
        assert "does not enroll" in sent[0][1]
        assert "withdrawn and erased" in sent[0][1]
    if name == "undertow":
        invoke(module, command, callback=True)
        assert len(sent) == 2


def test_group_routing_keeps_one_addressed_owner(bot, monkeypatch):
    name, module = bot
    sent = []
    monkeypatch.setattr(module, "send", lambda *a, **kw: sent.append(a))
    invoke(module, "/agents@some_other_bot", "supergroup")
    assert not sent
    invoke(module, "/agents", "supergroup")
    assert len(sent) == (0 if name in {"crypto", "narcoscope"} else 1)


def test_guide_does_not_echo_private_arguments(bot, monkeypatch):
    _, module = bot
    sent = []
    monkeypatch.setattr(module, "send", lambda *a, **kw: sent.append(a))
    invoke(module, "/data private-account-123")
    assert len(sent) == 1 and "private-account-123" not in sent[0][1]
