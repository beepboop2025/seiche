"""Add/remove only this service's Caddy import under the shared edge lock."""
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

remove = sys.argv[1:] == ["--remove"]
if sys.argv[1:] and not remove:
    raise SystemExit("Only --remove is supported")
line = "\timport /etc/caddy/agent-activity.caddy\n"
path = Path("/etc/caddy/Caddyfile")
with open("/run/lock/seiche-railway-edge.lock", "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    original = path.read_bytes()
    text = original.decode()
    if remove:
        updated = text.replace(line, "")
    elif line in text:
        updated = text
    else:
        if text.count("api.seiche.info {\n") != 1:
            raise SystemExit("Expected a single api.seiche.info site")
        updated = text.replace("api.seiche.info {\n", "api.seiche.info {\n"+line, 1)
    fd, name = tempfile.mkstemp(prefix="activity-candidate-", dir="/etc/caddy")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(updated)
        # Validation logs may contain unrelated configuration; keep them out of tool output.
        result = subprocess.run(["caddy", "validate", "--adapter", "caddyfile", "--config", name], capture_output=True)
        if result.returncode:
            raise SystemExit("Candidate Caddy validation failed; active configuration was not changed")
        if path.read_bytes() != original:
            raise SystemExit("Concurrent Caddy change detected; retry after reviewing it")
        if updated != text:
            backup = Path("/etc/seiche-agent-activity") / ("caddy-before-"+str(time.time_ns()))
            backup.write_bytes(original)
            backup.chmod(0o600)
            os.chmod(name, path.stat().st_mode & 0o777)
            os.chown(name, path.stat().st_uid, path.stat().st_gid)
            os.replace(name, path)
        reload = subprocess.run(["systemctl", "reload", "caddy"], capture_output=True)
        if reload.returncode:
            # The shared lock is still held. Preserve an unrelated writer if one ignored it.
            if path.read_text() == updated:
                path.write_bytes(original)
                subprocess.run(["systemctl", "reload", "caddy"], capture_output=True)
            raise SystemExit("Caddy reload failed; own configuration change rolled back when safe")
        print("activity_route_removed" if remove else "activity_route_published")
        print("caddy_sha256="+hashlib.sha256(path.read_bytes()).hexdigest())
    finally:
        Path(name).unlink(missing_ok=True)
