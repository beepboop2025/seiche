"""Contracts for dispatch writers that publish and deploy their commits."""

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
CANONICAL_SHA_CHECK = '[[ "${TARGET_SHA:-}" =~ ^[0-9a-f]{40}$ ]]'


def _step(workflow: str, name: str) -> str:
    start = workflow.index(f"      - name: {name}")
    end = workflow.find("\n      - name:", start + 1)
    return workflow[start:] if end == -1 else workflow[start:end]


@pytest.mark.parametrize(
    "workflow_path", DISPATCH_WORKFLOWS, ids=lambda path: path.stem
)
def test_dispatch_deploys_the_exact_commit_it_pushed(workflow_path: Path):
    workflow = workflow_path.read_text(encoding="utf-8")
    commit = _step(workflow, "Commit and push")
    trigger = _step(workflow, "Trigger site publish + box deploy")

    pushed = commit.index("git push origin main")
    resolved = commit.index('TARGET_SHA=$(git rev-parse --verify "HEAD^{commit}")')
    exported = commit.index("printf 'TARGET_SHA=%s\\nPUSHED=1\\n'")
    assert pushed < resolved < exported
    assert '[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]' in commit
    assert CANONICAL_SHA_CHECK in trigger
    assert f'{DEPLOY_CALL} --ref main --raw-field target_sha="$TARGET_SHA"' in trigger


def test_every_workflow_deploy_caller_supplies_the_required_target_sha():
    callers = {}
    for workflow_path in WORKFLOW_DIR.glob("*.yml"):
        lines = [
            line.strip()
            for line in workflow_path.read_text(encoding="utf-8").splitlines()
            if DEPLOY_CALL in line
        ]
        if lines:
            callers[workflow_path.name] = lines

    assert set(callers) == {path.name for path in DISPATCH_WORKFLOWS}
    assert all(
        '--raw-field target_sha="$TARGET_SHA"' in line
        for lines in callers.values()
        for line in lines
    )


def test_automatic_deploy_pushes_remain_bound_to_the_event_commit():
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert "push:\n    branches: [main]" in workflow
    assert (
        "target_sha:\n        description: Exact reviewed main commit to deploy"
        in workflow
    )
    assert "required: true" in workflow
    assert "TARGET_SHA: ${{ inputs.target_sha || github.sha }}" in workflow
