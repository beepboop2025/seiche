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
    tests = monitor / "test_monitor_roles.py"
    if not tests.is_file():
        raise ValueError("Recurring image lacks embedded monitor role tests")
    subprocess.run([sys.executable, "-B", str(tests)], cwd=monitor, check=True)
    return True


if __name__ == "__main__":
    qualify(Path(__file__).resolve().parent)
