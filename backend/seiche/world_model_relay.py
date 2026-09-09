"""Standalone loopback relay for one authenticated, opaque Lab export.

This process imports no API, database, collector, or model runtime. The existing
delivery validator remains authoritative for the credential and file boundary.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys

if __package__:
    from . import world_model_delivery as delivery
else:
    # The installer copies only these two root-owned modules. Isolated Python
    # deliberately omits cwd/PYTHONPATH; admit only the adjacent trusted module.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import world_model_delivery as delivery


class RelayHandler(BaseHTTPRequestHandler):
    """One bounded request per connection; credentials never enter logs."""

    timeout = 5
    server_version = "SeicheDeliveryRelay/1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # BaseHTTPRequestHandler otherwise logs attacker-controlled URLs.
        return

    def _respond(self, status: int, body: bytes, *, bearer: bool = False) -> None:
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        if bearer:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, detail: str, *, bearer: bool = False) -> None:
        self._respond(status, json.dumps({"detail": detail}).encode(), bearer=bearer)

    def do_GET(self) -> None:
        if sum(len(key) + len(value) for key, value in self.headers.items()) > 16384:
            self._error(431, "request headers too large")
            return
        if self.path != delivery.DELIVERY_ROUTE:
            self._error(404, "not found")
            return
        # The source is a GET-only byte relay, never a request-body consumer.
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or (
            lengths and lengths != ["0"]
        ):
            self._error(400, "invalid request")
            return
        config = delivery.configured_delivery()
        if config is None:
            self._error(404, "not found")
            return
        credentials = self.headers.get_all("Authorization", [])
        authorization = credentials[0] if len(credentials) == 1 else None
        if not delivery.bearer_authorized(config, authorization):
            self._error(401, "unauthorized", bearer=True)
            return
        try:
            opened = delivery.open_delivery(config)
            # At most HARD_MAX_BYTES, validated before any success headers.
            body = b"".join(delivery.iter_delivery(opened))
        except (delivery.DeliveryUnavailable, OSError):
            self._error(503, "signed delivery unavailable")
            return
        self._respond(200, body)

    def _not_found(self) -> None:
        self._error(404, "not found")

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _not_found
    do_TRACE = do_CONNECT = _not_found


class RelayServer(HTTPServer):
    allow_reuse_address = True
    request_queue_size = 16

    def handle_error(self, request: object, client_address: object) -> None:
        # Socket disconnects and malformed input must not dump request details.
        print("world-model relay: request failed", file=sys.stderr, flush=True)


def main() -> None:
    with RelayServer(("127.0.0.1", 8788), RelayHandler) as server:
        print("world-model relay: listening on loopback:8788", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
