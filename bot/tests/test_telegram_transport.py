"""Offline regressions for stalled IPv6 setup without shortening long polling."""

import importlib.util
import io
from pathlib import Path
import socket
import ssl
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
BOT_PATHS = ['bot/seiche_bot.py']


@pytest.fixture(params=BOT_PATHS, ids=lambda path: Path(path).stem)
def bot(request):
    path = ROOT / request.param
    sys.path.insert(0, str(ROOT))
    name = "transport_regression_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_stalled_ipv6_falls_back_before_long_poll_read_timeout(bot, monkeypatch):
    attempts, sockets, tls = [], [], []

    class Socket:
        def __init__(self, family, *_args):
            self.family, self.timeout, self.closed = family, None, False
            sockets.append(self)

        def settimeout(self, timeout):
            self.timeout = timeout

        def setsockopt(self, *_args):
            pass

        def connect(self, address):
            attempts.append((self.family, self.timeout, address))
            if self.family == socket.AF_INET6:
                raise TimeoutError("simulated stalled IPv6 connection")

        def close(self):
            self.closed = True

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    monkeypatch.setattr(socket, "socket", Socket)
    connection = bot._TelegramHTTPSConnection("api.telegram.org", timeout=60)
    assert connection._context.check_hostname
    assert connection._context.verify_mode == ssl.CERT_REQUIRED

    def wrap_socket(sock, server_hostname):
        tls.append((server_hostname, sock.timeout))
        return sock

    monkeypatch.setattr(connection._context, "wrap_socket", wrap_socket)
    connection.connect()
    assert [item[:2] for item in attempts] == [
        (socket.AF_INET6, 2.0), (socket.AF_INET, 2.0),
    ]
    assert sockets[0].closed
    assert tls == [("api.telegram.org", 2.0)]
    assert connection.timeout == connection.sock.timeout == 60


@pytest.mark.parametrize("requested, expected_connect, expected_read", [
    (55, 2.0, 55), (0.25, 0.25, 0.25), (None, 2.0, None),
    (socket._GLOBAL_DEFAULT_TIMEOUT, 0.75, 0.75),
])
def test_setup_cap_preserves_shorter_and_default_deadlines(
    bot, monkeypatch, requested, expected_connect, expected_read,
):
    seen = []
    monkeypatch.setattr(socket, "getdefaulttimeout", lambda: 0.75)

    class Socket:
        def settimeout(self, timeout):
            self.timeout = timeout

    def connect(connection):
        seen.append(connection.timeout)
        connection.sock = Socket()

    monkeypatch.setattr(bot.http.client.HTTPSConnection, "connect", connect)
    connection = bot._TelegramHTTPSConnection("api.telegram.org", timeout=requested)
    connection.connect()
    assert seen == [expected_connect]
    assert connection.timeout == connection.sock.timeout == expected_read


def test_tls_failure_propagates_without_retry_or_deadline_leak(bot, monkeypatch):
    attempts = []

    def reject(connection):
        attempts.append(connection.timeout)
        raise ssl.SSLCertVerificationError("untrusted fixture certificate")

    monkeypatch.setattr(bot.http.client.HTTPSConnection, "connect", reject)
    connection = bot._TelegramHTTPSConnection("api.telegram.org", timeout=60)
    with pytest.raises(ssl.SSLCertVerificationError):
        connection.connect()
    assert attempts == [2.0]
    assert connection.timeout == 60


def test_bot_uses_telegram_only_opener_and_preserves_poll_read_window(bot, monkeypatch):
    calls = []

    def respond(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(b'{"ok":true,"result":[]}')

    monkeypatch.setattr(bot._TELEGRAM_OPENER, "open", respond)
    monkeypatch.setattr(bot.urllib.request, "urlopen", lambda *_a, **_k:
                        pytest.fail("Telegram bypassed its bounded transport"))
    handlers = [handler for handler in bot._TELEGRAM_OPENER.handlers
                if isinstance(handler, bot.urllib.request.HTTPSHandler)]
    assert len(handlers) == 1
    assert isinstance(handlers[0], bot._TelegramHTTPSHandler)
    assert bot.tg_call("getUpdates", {"timeout": 50})["ok"] is True
    assert calls == [(bot.TG + "/getUpdates", 55 if bot.__name__.endswith("crypto_bot") else 60)]


def test_ambiguous_telegram_send_failure_is_not_replayed_by_transport(bot, monkeypatch):
    calls = []

    def timeout(request, **_kwargs):
        calls.append(request.full_url)
        raise TimeoutError("response was lost after request submission")

    monkeypatch.setattr(bot._TELEGRAM_OPENER, "open", timeout)
    assert bot.tg_call("sendMessage", {"chat_id": 1, "text": "fixture"}) is None
    assert calls == [bot.TG + "/sendMessage"]


def test_photos_use_the_same_bounded_transport(bot, monkeypatch):
    if not hasattr(bot, "send_photo"):
        pytest.skip("this bot does not send photos")
    calls = []

    def respond(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(b'{"ok":true}')

    monkeypatch.setattr(bot._TELEGRAM_OPENER, "open", respond)
    assert bot.send_photo(1, b"fixture-png", "fixture") is True
    assert calls == [(bot.TG + "/sendPhoto", 60)]
