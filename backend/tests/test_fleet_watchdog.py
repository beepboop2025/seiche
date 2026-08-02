"""The watchdog's own failure modes.

The alarm is the last thing that notices a silent bot, so the ways it can go
quiet matter more than the ways a bot can: a config that does not parse, a
state file someone truncated, a probe that raises. Each of those used to take
the whole run down before save_state(), which meant no counter advanced and no
alert could ever fire again: the "alive but answering nothing" failure, in the
watcher itself.

Loaded by path because it ships as a standalone script to /opt on the box, not
as part of this package.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "ops" / "fleet-watchdog" / "watchdog.py"


def _load():
    spec = importlib.util.spec_from_file_location("fleet_watchdog", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wd = _load()


# ---- config -----------------------------------------------------------------

_GOOD = {
    "default_alert_via": "alpha-bot",
    "bots": [
        {"unit": "alpha-bot", "env": "/etc/a.env", "var": "A", "alert_via": "beta-bot"},
        {"unit": "beta-bot", "env": "/etc/b.env", "var": "B",
         "state": "/var/lib/b/offset.json"},
    ],
    "mcp_remotes": [{"name": "mcp-a", "url": "https://example.test/mcp"}],
}


def _write(tmp_path, payload) -> str:
    p = tmp_path / "cfg.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(p)


def test_config_round_trips(tmp_path):
    cfg = wd.load_config(_write(tmp_path, _GOOD))
    assert [b.unit for b in cfg.bots] == ["alpha-bot", "beta-bot"]
    assert cfg.bots[0].via == "beta-bot"
    assert cfg.bots[1].state == "/var/lib/b/offset.json"
    assert cfg.bots[0].state is None
    assert [r.name for r in cfg.remotes] == ["mcp-a"]


@pytest.mark.parametrize("payload", ["[]", "null", "not json at all", '{"bots": 3}'])
def test_malformed_config_yields_empty_not_an_exception(tmp_path, payload):
    cfg = wd.load_config(_write(tmp_path, payload))
    assert cfg.bots == [] and cfg.remotes == []


def test_one_bad_entry_does_not_drop_the_good_ones(tmp_path):
    bad = {"bots": [{"unit": "ok", "env": "/etc/ok.env", "var": "OK"},
                    {"unit": "no-env-path"},
                    "a string where an object belongs"]}
    cfg = wd.load_config(_write(tmp_path, bad))
    assert [b.unit for b in cfg.bots] == ["ok"]


def test_missing_config_file_is_survivable(tmp_path):
    cfg = wd.load_config(str(tmp_path / "absent.json"))
    assert cfg.bots == [] and cfg.remotes == []


def test_alert_never_routes_through_the_thing_being_reported(tmp_path):
    cfg = wd.load_config(_write(tmp_path, _GOOD))
    # configured value wins
    assert wd.pick_via("beta-bot", "alpha-bot", cfg) == "alpha-bot"
    # a self-referential config entry is ignored, not obeyed
    assert wd.pick_via("alpha-bot", "alpha-bot", cfg) == "beta-bot"
    # and so is a self-referential default
    assert wd.pick_via("alpha-bot", "", cfg) == "beta-bot"
    # anything else falls back to the default sender
    assert wd.pick_via("mcp-a", "", cfg) == "alpha-bot"


def test_read_env_survives_a_json_config_of_the_wrong_shape(tmp_path):
    p = tmp_path / "bot.json"
    p.write_text("[1, 2, 3]")
    assert wd.read_env(str(p), "bot_token") == ""


def test_read_env_reads_both_formats(tmp_path):
    env = tmp_path / "bot.env"
    env.write_text("# comment\nOTHER=x\nTOK='secret'\n")
    assert wd.read_env(str(env), "TOK") == "secret"
    js = tmp_path / "bot.json"
    js.write_text(json.dumps({"bot_token": "secret"}))
    assert wd.read_env(str(js), "bot_token") == "secret"


# ---- state ------------------------------------------------------------------

@pytest.mark.parametrize("payload", ["[]", "null", "3", "{oops"])
def test_load_state_returns_a_dict_whatever_the_file_says(tmp_path, monkeypatch, payload):
    p = tmp_path / "state.json"
    p.write_text(payload)
    monkeypatch.setattr(wd, "STATE_PATH", str(p))
    assert wd.load_state() == {}


# ---- probe verdicts ---------------------------------------------------------

_URL = "https://example.test/mcp"


def _init_ok(extra=None):
    return json.dumps({"jsonrpc": "2.0", "id": 1,
                       "result": {"protocolVersion": "2025-06-18",
                                  "serverInfo": {"name": "x", "version": "1"},
                                  **(extra or {})}})


def test_a_real_initialize_reply_is_healthy():
    assert wd._verdict(_URL, _init_ok()) == []


def test_an_sse_framed_reply_is_healthy():
    assert wd._verdict(_URL, f"event: message\ndata: {_init_ok()}\n\n") == []


def test_a_jsonrpc_error_still_proves_an_mcp_server_answered():
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32600, "message": "bad request"}})
    assert wd._verdict(_URL, body) == []


def test_an_echo_of_the_probe_is_not_proof_of_health():
    """A substring scan passed this: the probe's own body carries 'jsonrpc'."""
    assert wd._verdict(_URL, json.dumps(wd.MCP_INIT)) != []


def test_a_result_without_serverinfo_is_not_an_mcp_server():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}})
    assert wd._verdict(_URL, body) != []


def test_an_error_message_containing_the_word_result_is_not_healthy():
    """The old scan asked for '\"error\"' without '\"result\"' anywhere in the
    text, so an error whose message used the word was read as a success."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "error": {"code": -32000,
                                 "message": 'no "result" could be produced'}})
    assert wd._verdict(_URL, body) == []  # parsed: an error envelope, server up
    plain = 'upstream said: no "result" could be produced (jsonrpc)'
    assert wd._verdict(_URL, plain) != []  # unparseable: reported, not waved through


def test_html_from_a_misroute_is_reported():
    problems = wd._verdict(_URL, "<html><body>welcome to nginx</body></html>")
    assert problems and "wrong service" in problems[0]


# ---- bounded read -----------------------------------------------------------

class _Trickle:
    """An SSE stream that answers, then keeps the connection open forever."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.reads = 0

    def read1(self, _n):
        self.reads += 1
        if self.frames:
            return self.frames.pop(0)
        raise AssertionError("read past the answer on a stream held open")

    read = read1


def test_reading_stops_at_the_first_complete_message():
    stream = _Trickle([b"event: message\n", f"data: {_init_ok()}\n\n".encode()])
    assert wd._verdict(_URL, wd._read_bounded(stream)) == []
    assert stream.reads == 2


def test_reading_gives_up_rather_than_hanging_on_a_keepalive_stream(monkeypatch):
    monkeypatch.setattr(wd, "MCP_READ_DEADLINE_S", 0.05)

    class _Keepalive:
        def read1(self, _n):
            return b": keepalive\n"

    assert "jsonrpc" not in wd._read_bounded(_Keepalive())


# ---- the run itself ---------------------------------------------------------

def test_a_raising_probe_costs_one_check_not_the_run():
    def boom():
        raise RuntimeError("kaboom")

    problems = wd.guarded("alpha-bot", boom)
    assert problems and "RuntimeError" in problems[0]


def test_no_owner_chat_exits_non_zero(monkeypatch):
    monkeypatch.setattr(wd, "OWNER_CHAT", "")
    assert wd.main() == 1


def test_nothing_configured_exits_non_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", str(tmp_path / "absent.json"))
    assert wd.main() == 1


def test_every_remote_unreachable_is_one_alarm_not_n(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"), "var": "A"}],
        "mcp_remotes": [{"name": f"mcp-{i}", "url": f"https://{i}.test/mcp"}
                        for i in range(6)],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(tmp_path / "absent.heartbeat"))
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "check_mcp", lambda url: ["unreachable (gaierror)"])
    monkeypatch.setattr(wd, "notify", lambda cfg, via, text: sent.append(text) or True)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)

    assert wd.main() == 0
    assert len(sent) == 1
    assert "all 6 remotes unreachable" in sent[0]


def test_a_single_remote_outage_still_names_that_remote(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "default_alert_via": "alpha-bot",
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"), "var": "A"}],
        "mcp_remotes": [{"name": "mcp-a", "url": "https://a.test/mcp"},
                        {"name": "mcp-b", "url": "https://b.test/mcp"}],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(tmp_path / "absent.heartbeat"))
    monkeypatch.setattr(wd, "check", lambda bot: [])
    monkeypatch.setattr(wd, "check_mcp",
                        lambda url: ["HTTP 502 on initialize"] if "a.test" in url else [])
    monkeypatch.setattr(wd, "notify", lambda cfg, via, text: sent.append(text) or True)
    monkeypatch.setattr(wd, "CONSECUTIVE", 1)

    assert wd.main() == 0
    assert len(sent) == 1 and "mcp-a" in sent[0]


def test_a_corrupt_state_entry_does_not_stop_the_run(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"alpha-bot": "this used to be a dict"}))
    monkeypatch.setattr(wd, "OWNER_CHAT", "123")
    monkeypatch.setattr(wd, "CONFIG_PATH", _write(tmp_path, {
        "bots": [{"unit": "alpha-bot", "env": str(tmp_path / "a.env"), "var": "A"}],
    }))
    monkeypatch.setattr(wd, "STATE_PATH", str(state))
    monkeypatch.setattr(wd, "MAC_HEARTBEAT", str(tmp_path / "absent.heartbeat"))
    monkeypatch.setattr(wd, "check", lambda bot: [])

    assert wd.main() == 0
    assert isinstance(json.loads(state.read_text())["alpha-bot"], dict)
