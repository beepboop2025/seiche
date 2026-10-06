"""Require embedded role qualification for the declared recurring image mode."""
import json
from pathlib import Path
import subprocess
import sys


def qualify(root):
    policy = json.loads((root / "policy.json").read_bytes())
    if "operation" not in policy:
        # The original historical-only assembler has no embedded monitor.
        return False
    if policy["operation"] != "export-recurring":
        raise ValueError("Unknown recovery image operation")
    monitor = root / "monitor"
    tests = [monitor / name for name in ("test_monitor_roles.py", "test_health_wait.py")]
    if not all(path.is_file() for path in tests) or not (monitor / "health_wait.py").is_file():
        raise ValueError("Recurring image lacks embedded monitor role or health tests")
    for path in tests:
        subprocess.run([sys.executable, "-B", str(path)], cwd=monitor, check=True)
    return True


if __name__ == "__main__":
    qualify(Path(__file__).resolve().parent)
