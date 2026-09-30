"""The native monitor's pinned subset must contain its complete import closure."""

import ast
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_prepared_monitor_subset_runs_real_pair_parser(tmp_path):
    path = ROOT / "deploy/railway-ci/recovery-monitor/prepare.py"
    assignments = [
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "INPUTS"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    assert len(assignments) == 1, "Expected exactly one top-level INPUTS assignment"
    inputs = ast.literal_eval(assignments[0].value)
    assert isinstance(inputs, list) and inputs
    assert all(isinstance(name, str) for name in inputs)
    for name in inputs:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)

    # Isolated Python cannot satisfy a missing prepared module from the checkout.
    probe = """
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from seiche import stateful_control
assert Path(stateful_control.__file__).is_relative_to(Path(sys.argv[1]))
try:
    stateful_control.extract_latest_recovery_pair(
        b"", expected_commit="0" * 40,
        expected_deployment_id="00000000-0000-4000-8000-000000000000",
        now=datetime.now(timezone.utc),
    )
except stateful_control.ControlContractError:
    pass
else:
    raise AssertionError("The real monitor parser accepted missing proof")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(tmp_path / "backend")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
