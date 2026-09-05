"""Exercise the isolated container launcher without starting runtime services."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_isolated_launcher_loads_verified_source_and_governance(tmp_path: Path) -> None:
    dockerfile = (ROOT / "ops/railway/Dockerfile.stateful").read_text()
    entrypoint = json.loads(
        next(
            line.removeprefix("ENTRYPOINT ")
            for line in dockerfile.splitlines()
            if line.startswith("ENTRYPOINT ")
        )
    )
    assert entrypoint[:6] == ["/usr/bin/tini", "--", "python", "-I", "-B", "-c"]
    assert len(entrypoint) == 7
    # Only relocate the image's verified workspace for this host-side test.
    bootstrap = entrypoint[6].replace("/workspace/backend", str(ROOT / "backend"))
    poison = tmp_path / "untrusted"
    package = poison / "seiche"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('untrusted package loaded')\n"
    )
    driver = f"""
import pathlib, sys
assert sys.flags.isolated == 1
assert {str(poison)!r} not in sys.path
# Simulate an installed package preceding the standard import locations.
sys.path.insert(0, {str(poison)!r})

def before_runtime(frame, event, arg):
    if event != 'call' or frame.f_code.co_name != 'run':
        return before_runtime
    if frame.f_globals.get('__spec__', None) is None:
        return before_runtime
    if frame.f_globals['__spec__'].name != 'seiche.stateful_entrypoint':
        return before_runtime
    sys.settrace(None)
    from seiche import stateful_control
    assert pathlib.Path(stateful_control.__file__).resolve() == pathlib.Path(
        {str(ROOT / "backend/seiche/stateful_control.py")!r}
    )
    assert stateful_control.SIGNER_REGISTRY_PATH == pathlib.Path(
        {str(ROOT / "governance/railway-control-signers.json")!r}
    )
    stateful_control.load_signer_registry()
    print('verified-source-and-registry')
    raise SystemExit(0)

sys.settrace(before_runtime)
exec({bootstrap!r})
raise AssertionError('entrypoint did not reach its closed dispatcher')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", driver],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(poison)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "verified-source-and-registry"
