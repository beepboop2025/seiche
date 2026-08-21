"""Contracts for dispatch writers that publish without release authority."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
DEPLOY_WORKFLOW = WORKFLOW_DIR / "deploy-hetzner.yml"
DISPATCH_WORKFLOWS = (
    WORKFLOW_DIR / "dispatch-daily.yml",
    WORKFLOW_DIR / "dispatch-weekly.yml",
)
DEPLOY_CALL = "gh workflow run deploy-hetzner.yml"


def _step(workflow: str, name: str) -> str:
    start = workflow.index(f"      - name: {name}")
    end = workflow.find("\n      - name:", start + 1)
    return workflow[start:] if end == -1 else workflow[start:end]


@pytest.mark.parametrize(
    "workflow_path", DISPATCH_WORKFLOWS, ids=lambda path: path.stem
)
def test_dispatch_pushes_content_and_triggers_only_the_static_site(workflow_path: Path):
    workflow = workflow_path.read_text(encoding="utf-8")
    commit = _step(workflow, "Commit and push")
    trigger = _step(workflow, "Trigger site publish")

    assert commit.index("git push origin main") < commit.index("printf 'PUSHED=1\\n'")
    assert "TARGET_SHA" not in commit
    assert "gh workflow run publish.yml --ref main" in trigger
    assert DEPLOY_CALL not in trigger


def test_no_workflow_invokes_the_disabled_legacy_controller():
    callers = []
    for workflow_path in WORKFLOW_DIR.glob("*.yml"):
        callers.extend(
            (workflow_path.name, line.strip())
            for line in workflow_path.read_text(encoding="utf-8").splitlines()
            if DEPLOY_CALL in line
        )
    assert callers == []


def test_legacy_deploy_is_manual_only_and_requires_an_exact_target():
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "push:\n    branches: [main]" not in workflow
    assert (
        "target_sha:\n        description: Exact reviewed main commit to deploy"
        in workflow
    )
    assert "required: true" in workflow
    assert "TARGET_SHA: ${{ inputs.target_sha || github.sha }}" in workflow
