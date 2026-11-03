"""The Seiche bot authenticates /ask with a Seiche account token from the environment.

Seiche's /api/ask is account-only: its MCP twin ask_desk needs a bearer token.
The bot reads SEICHE_BOT_DESK_TOKEN and sends it only as the Authorization
header of the /ask request. It never puts it in a URL, a message or a log line,
and never follows a redirect with it. Without the variable the request goes out
as before and a refusal keeps the graceful "did not answer" reply.
"""
from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "seiche-desk-test-token-not-a-real-token"
ANSWER = {"ok": True, "answer": "Overnight cash is orderly.", "grounding": "live board"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _load(monkeypatch, tmp_path, token):
    if token is None:
        monkeypatch.delenv("SEICHE_BOT_DESK_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SEICHE_BOT_DESK_TOKEN", token)
    spec = importlib.util.spec_from_file_location(
        "desk_token_seiche_bot", ROOT / "bot" / "seiche_bot.py")
    assert spec and spec.loader
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    monkeypatch.setattr(bot, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(bot.time, "sleep", lambda _seconds: None)
    return bot


def _refuse(request, status: int, body: bytes = b'{"detail": "this route needs a bearer token"}'):
    raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(body))


def test_the_ask_request_carries_the_token_only_in_its_header(monkeypatch, tmp_path, capsys):
    bot = _load(monkeypatch, tmp_path, TOKEN)
    seen = []

    class Opener:
        def open(self, request, timeout=None):
            seen.append(request)
            return _Response(json.dumps(ANSWER).encode())

    built = []
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *handlers: built.append(handlers) or Opener())
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_a, **_k: pytest.fail("a token request must not follow redirects"))

    assert bot.ask_desk("Is dollar funding tight?") == ANSWER
    (request,) = seen
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert TOKEN not in request.full_url
    assert built == [(bot._DeskNoRedirect,)]
    assert TOKEN not in "".join(capsys.readouterr())


def test_a_refused_or_redirected_token_request_is_logged_without_the_token(monkeypatch, tmp_path, capsys):
    bot = _load(monkeypatch, tmp_path, TOKEN)
    for status in (401, 302):
        class Opener:
            def open(self, request, timeout=None, status=status):
                _refuse(request, status)

        monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: Opener())
        assert bot.ask_desk("Is dollar funding tight?") is None
        output = "".join(capsys.readouterr())
        assert f"failed: {status}" in output
        assert TOKEN not in output and "Bearer" not in output


def test_the_desk_redirect_handler_never_follows(monkeypatch, tmp_path):
    bot = _load(monkeypatch, tmp_path, TOKEN)
    request = urllib.request.Request("https://api.seiche.info/api/ask?q=x",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
    assert bot._DeskNoRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://elsewhere.example/") is None


def test_without_the_variable_the_request_is_unchanged_and_the_reply_stays_graceful(
        monkeypatch, tmp_path, capsys):
    bot = _load(monkeypatch, tmp_path, None)
    assert bot.DESK_TOKEN == ""
    seen = []

    def urlopen(request, timeout=None):
        seen.append(request)
        _refuse(request, 401)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *_a: pytest.fail("no token, no special opener"))
    result = bot.ask_desk("Is dollar funding tight?")
    assert result is None
    assert seen and seen[0].get_header("Authorization") is None
    assert bot.fmt_ask(result) == "The desk assistant did not answer. Try again shortly."
    assert "failed: 401" in "".join(capsys.readouterr())


def test_the_token_is_read_from_the_environment_never_hard_coded():
    source = (ROOT / "bot" / "seiche_bot.py").read_text(encoding="utf-8")
    assert 'os.environ.get("SEICHE_BOT_DESK_TOKEN", "")' in source
    assert source.count("DESK_TOKEN") >= 3
    assert "Bearer {DESK_TOKEN}" in source
    # It is only ever placed in the Authorization header.
    assert "print(" not in "".join(line for line in source.splitlines() if "DESK_TOKEN" in line)
