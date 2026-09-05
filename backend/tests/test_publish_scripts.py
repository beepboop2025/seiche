"""Static-publish scripts must reuse one captured board without rebuilding it."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

from seiche import publisher


ROOT = Path(__file__).resolve().parents[2]


def _script(name: str):
    path = ROOT / "backend" / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"test_{name.removesuffix('.py')}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot() -> dict:
    return {
        "generated_at": "2026-08-11T18:48:39+00:00",
        "engines": {
            "composite": {
                "ok": True,
                "value": 27.0,
                "regime": "WATCH",
                "coverage_pct": 100.0,
            }
        },
        "deep": {
            "book": {
                "ok": True,
                "today": {
                    "stance": "neutral",
                    "positions": [{"sleeve": "cash", "weight": 1.0}],
                    "p_ensemble": 0.08,
                    "dispersion": 0.02,
                },
            }
        },
        "editorial": {},
        "data_quality": {},
    }


def _forbid_rebuild(monkeypatch: pytest.MonkeyPatch, module) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("snapshot input must not rebuild the board")

    monkeypatch.setattr(module.assemble, "snapshot", forbidden)


def test_export_public_reuses_the_exact_snapshot(tmp_path, monkeypatch):
    module = _script("export_public.py")
    _forbid_rebuild(monkeypatch, module)
    snapshot = _snapshot()
    snapshot_path = tmp_path / "captured.json"
    public_path = tmp_path / "public.json"
    overview_path = tmp_path / "overview.json"
    snapshot_path.write_text(json.dumps(snapshot))

    result = module.main(
        ["--snapshot", str(snapshot_path), str(public_path), str(overview_path)]
    )

    assert result == 0
    assert json.loads(overview_path.read_text()) == snapshot
    public = json.loads(public_path.read_text())
    assert public["generated_at"] == snapshot["generated_at"]
    assert public["conclusion"]["regime"] == "WATCH"


def test_append_book_record_reuses_the_exact_snapshot(tmp_path, monkeypatch):
    module = _script("append_book_record.py")
    _forbid_rebuild(monkeypatch, module)
    snapshot = _snapshot()
    snapshot_path = tmp_path / "captured.json"
    output_path = tmp_path / "book_history.json"
    snapshot_path.write_text(json.dumps(snapshot))

    result = module.main(["--snapshot", str(snapshot_path), "-", str(output_path)])

    assert result == 0
    history = json.loads(output_path.read_text())
    assert len(history) == 1
    assert history[0]["date"] == "2026-08-11"
    assert history[0]["index"] == 27.0
    assert history[0]["prev_hash"] == publisher.GENESIS
    assert publisher.verify_chain(history)[0]


@pytest.mark.parametrize(
    "body, message",
    [
        ("[]", "must be a JSON object"),
        ('{"generated_at":"2026-08-11T00:00:00Z","engines":{"x":NaN}}', "non-finite"),
        ('{"generated_at":"2026-08-11T00:00:00Z"}', "no engines object"),
        ('{"generated_at":"2026-08-11","engines":{}}', "no timezone"),
        (
            '{"generated_at":"2026-08-11T00:00:00Z","engines":{},"engines":{}}',
            "duplicate JSON key",
        ),
    ],
)
def test_snapshot_input_fails_before_overwriting_outputs(
    tmp_path, monkeypatch, capsys, body, message
):
    module = _script("export_public.py")
    _forbid_rebuild(monkeypatch, module)
    snapshot_path = tmp_path / "bad.json"
    output_path = tmp_path / "public.json"
    snapshot_path.write_text(body)
    output_path.write_text("last-known-good")

    result = module.main(["--snapshot", str(snapshot_path), str(output_path)])

    assert result == 1
    assert output_path.read_text() == "last-known-good"
    assert message in capsys.readouterr().err


def test_static_publish_reuses_its_exported_board_for_the_book():
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
    exported = workflow.index(
        "export_public.py frontend/public/data/public.json "
        "frontend/public/data/overview.json"
    )
    appended = workflow.index("python backend/scripts/append_book_record.py")

    assert exported < appended
    assert "--snapshot frontend/public/data/overview.json" in workflow[appended:]


def test_static_publish_reuses_only_an_exact_sha_gate():
    """A green gate binds both the tested source and its workflow controller."""
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()

    restored = workflow.index("name: Restore exact-code publish gate")
    verified = workflow.index("name: Verify exact-code publish gate")
    tested = workflow.index("name: Engine tests (publish gates on green)")
    exported = workflow.index("name: Run engines, export snapshot")
    restore_block = workflow[restored:verified]

    assert restored < verified < tested < exported
    identity_path = "${{ env.PUBLICATION_SOURCE_SHA }}-${{ env.PUBLICATION_CONTROLLER_SHA }}"
    assert f"path: .cache/publish-gates/{identity_path}" in restore_block
    assert f"seiche-publish-gate-v2-${{{{ runner.os }}}}-py312-{identity_path}" in restore_block
    assert "restore-keys:" not in restore_block
    assert "[ \"$CACHE_HIT\" = \"true\" ]" in workflow
    assert f"MARKER: .cache/publish-gates/{identity_path}/validated-sha" in workflow[verified:tested]
    assert '= "$PUBLICATION_SOURCE_SHA:$PUBLICATION_CONTROLLER_SHA"' in workflow[verified:tested]
    assert "if: steps.publish-gate.outputs.run-full-suite == 'true'" in workflow
    assert 'printf \'%s:%s\\n\' "$PUBLICATION_SOURCE_SHA" "$PUBLICATION_CONTROLLER_SHA"' in workflow[tested:exported]
    assert '".cache/publish-gates/$PUBLICATION_SOURCE_SHA-$PUBLICATION_CONTROLLER_SHA/validated-sha"' in workflow[tested:exported]
    assert "-o faulthandler_timeout=300" in workflow[tested:exported]
    assert "--pystack-threshold" not in workflow[tested:exported]
    assert workflow.count('if [ "$current_main" != "$PUBLICATION_SOURCE_SHA" ]; then') == 2
    assert 'test "$(git rev-parse \'refs/remotes/origin/main^{commit}\')" = "$PUBLICATION_SOURCE_SHA"' in workflow


@pytest.mark.parametrize(
    ("cache_hit", "marker_identity", "run_full_suite"),
    [
        ("true", "a" * 40 + ":" + "b" * 40, "false"),
        ("false", "a" * 40 + ":" + "b" * 40, "true"),
        ("true", "c" * 40 + ":" + "b" * 40, "true"),
        ("true", "a" * 40 + ":" + "c" * 40, "true"),
        ("true", "a" * 40, "true"),
        ("true", None, "true"),
    ],
)
def test_publish_gate_marker_requires_both_exact_identities(
    tmp_path, cache_hit, marker_identity, run_full_suite
):
    workflow = (ROOT / ".github/workflows/publish.yml").read_text()
    verification = workflow.split("      - name: Verify exact-code publish gate\n", 1)[1]
    verification = verification.split("\n      - name:", 1)[0]
    program = textwrap.dedent(verification.split("        run: |\n", 1)[1])
    marker = tmp_path / "validated-sha"
    if marker_identity is not None:
        marker.write_text(marker_identity + "\n")
    output = tmp_path / "github-output"
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", program],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CACHE_HIT": cache_hit,
            "MARKER": str(marker),
            "PUBLICATION_SOURCE_SHA": "a" * 40,
            "PUBLICATION_CONTROLLER_SHA": "b" * 40,
            "GITHUB_OUTPUT": str(output),
        },
    )
    assert output.read_text().splitlines() == [f"run-full-suite={run_full_suite}"]


def test_signed_controller_test_support_is_restored_before_export():
    workflow = (ROOT / ".github/workflows/publish.yml").read_text()
    tested = workflow.index("name: Engine tests (publish gates on green)")
    exported = workflow.index("name: Run engines, export snapshot")
    block = workflow[tested:exported]
    assert 'trap restore_test_support EXIT' in block
    verified = block.index('verify-commit "$PUBLICATION_CONTROLLER_SHA"')
    restricted = block.index('if not changed or not changed <= allowed:')
    overlaid = block.index('git restore --source="$PUBLICATION_CONTROLLER_SHA" -- "${test_support[@]}"')
    suite = block.index('python -m pytest backend/tests -q --memray -o faulthandler_timeout=300')
    restored = block.index('\n          restore_test_support\n', suite)
    pristine = block.index('test -z "$(git status --porcelain --untracked-files=no)"', restored)
    marker = block.index('printf \'%s:%s\\n\'', pristine)
    assert verified < restricted < overlaid < suite < restored < pristine < marker
    assert 'git restore --source="$PUBLICATION_SOURCE_SHA" -- "${test_support[@]}"' in block
    assert 'git merge-base --is-ancestor "$PUBLICATION_SOURCE_SHA" "$PUBLICATION_CONTROLLER_SHA"' in block
    assert 'if mode != "100644":' in block
    assert '-k ' not in block and '--ignore' not in block and '--deselect' not in block
