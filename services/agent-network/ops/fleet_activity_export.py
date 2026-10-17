"""Bridge the fixed print-only host digest to the unprivileged Wake collector."""
from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    destination = Path("/var/lib/seiche-agent-network/fleet/digest.json")
    # Never use the digest's default invocation: that would send Telegram.
    result = subprocess.run(
        ["/usr/bin/python3", "-B", "/opt/fleet-usage/usage_digest.py",
         "--hours", "24", "--print-only"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45, check=True,
    )
    if len(result.stdout) > 1_000_000:
        raise ValueError("fleet aggregate exceeds bound")
    report = {"schema": "wake.fleet-digest.v1", "window_hours": 24,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "digest": result.stdout.decode("utf-8")}
    fd, name = tempfile.mkstemp(prefix=".digest-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o640)
            os.fchown(handle.fileno(), 0, grp.getgrnam("seiche-network").gr_gid)
            json.dump(report, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
