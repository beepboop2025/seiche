"""Exercise the route example against a real Caddy process and a disposable origin."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest


def request(port, method, path):
    client = HTTPConnection("127.0.0.1", port, timeout=12)
    try:
        client.request(method, path)
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


@unittest.skipUnless(shutil.which("caddy"), "requires the Caddy proxy binary")
class GatewayRestartTests(unittest.TestCase):
    def test_short_restart_long_outage_and_excluded_requests(self):
        received = []

        class Origin(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(("GET", self.path))
                body = self.path.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                received.append(("POST", self.path))
                self.send_error(405)

            def log_message(self, *args):
                pass

        with socket.socket() as backend_port, socket.socket() as frontend_port:
            backend_port.bind(("127.0.0.1", 0))
            frontend_port.bind(("127.0.0.1", 0))
            origin_port = backend_port.getsockname()[1]
            proxy_port = frontend_port.getsockname()[1]
        with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(
            ("127.0.0.1", origin_port), Origin, bind_and_activate=False
        ) as origin:
            route = Path(__file__).parents[1].joinpath("ops/caddy-route.example").read_text()
            route = route.replace("127.0.0.1:18796", f"127.0.0.1:{origin_port}")
            config = Path(temporary, "Caddyfile")
            config.write_text("{\n admin off\n auto_https off\n}\n" +
                f"http://127.0.0.1:{proxy_port} {{\nrespond /proxy-ready 200\n{route}\n}}\n")
            env = {"HOME": temporary, "PATH": os.defpath, "XDG_CONFIG_HOME": temporary,
                   "XDG_DATA_HOME": temporary}
            with Path(temporary, "caddy.log").open("w+") as log:
                proxy = subprocess.Popen([shutil.which("caddy"), "run", "--config", str(config)],
                                         stdout=log, stderr=log, env=env)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        if proxy.poll() is not None or time.monotonic() > deadline:
                            log.seek(0)
                            self.fail("Caddy failed to start: " + log.read()[-1000:])
                        try:
                            if request(proxy_port, "GET", "/proxy-ready")[0] == 200:
                                break
                        except OSError:
                            pass
                        time.sleep(.05)

                    # Leave the origin port closed until the simulated restart completes.
                    started = time.monotonic()
                    self.assertEqual(request(proxy_port, "POST", "/agents/healthz")[0], 502)
                    self.assertEqual(request(proxy_port, "GET", "/agents/unlisted")[0], 502)
                    self.assertLess(time.monotonic() - started, 3)
                    paths = ("/healthz", "/v1/context/contract", "/v1/context/snapshot")
                    with ThreadPoolExecutor(max_workers=len(paths)) as clients:
                        queued = [clients.submit(request, proxy_port, "GET", "/agents" + path) for path in paths]
                        time.sleep(2)
                        self.assertTrue(all(not future.done() for future in queued))
                        origin.server_bind()
                        origin.server_activate()
                        thread = threading.Thread(target=origin.serve_forever, daemon=True)
                        thread.start()
                        try:
                            for path, result in zip(paths, queued):
                                self.assertEqual(result.result(timeout=8), (200, path.encode()))
                        finally:
                            origin.shutdown()
                            thread.join(3)
                            origin.server_close()
                    self.assertCountEqual(received, [("GET", path) for path in paths])
                    started = time.monotonic()
                    self.assertEqual(request(proxy_port, "GET", "/agents/healthz")[0], 502)
                    self.assertGreaterEqual(time.monotonic() - started, 5)
                    self.assertLess(time.monotonic() - started, 10)
                finally:
                    proxy.terminate()
                    try:
                        proxy.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proxy.kill()
                        proxy.wait()
