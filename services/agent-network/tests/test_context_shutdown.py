"""Exercise the real CLI and an open SSE connection across process shutdown."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from seiche_agent_network.crypto import generate_keys


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM lifecycle requires POSIX")
def test_cli_sigterm_bounds_open_stream_and_completes_application_cleanup(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps(generate_keys()))
    keys.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    service = Path(__file__).resolve().parents[1]
    base = f"http://127.0.0.1:{port}"
    command = [sys.executable, "-m", "seiche_agent_network.cli", "--database",
               str(tmp_path / "network.sqlite3"), "--keys-file", str(keys),
               "serve", "--host", "127.0.0.1", "--port", str(port),
               "--public-url", base, "--gateway"]
    # The subprocess cannot inherit production database URLs or service keys.
    env = {"PATH": os.defpath, "PYTHONPATH": str(service / "src"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    stream = None
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(command, env=env, cwd=tmp_path,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, "CLI exited before accepting requests"
                try:
                    with urlopen(base + "/healthz", timeout=.5) as response:
                        assert response.status == 200
                    break
                except (URLError, TimeoutError):
                    assert time.monotonic() < deadline, "CLI startup timed out"
                    time.sleep(.05)
            stream = urlopen(base + "/v1/context/events", timeout=15)
            assert stream.status == 200
            frame = bytearray()
            while not frame.endswith(b"\n\n"):
                chunk = stream.read(1)
                assert chunk, "SSE closed before its initial reset"
                frame.extend(chunk)
                assert len(frame) <= 17408
            assert b"event: reset\n" in frame
            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            # Keep the SSE socket open: closing it here would hide the drain bug.
            process.wait(timeout=15)
            assert time.monotonic() - started < 15
            assert process.returncode in (0, -signal.SIGTERM)
            log.seek(0)
            assert "Application shutdown complete." in log.read()
        finally:
            if stream is not None:
                stream.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
