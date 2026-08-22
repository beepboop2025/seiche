"""Static-publish scripts must reuse one captured board without rebuilding it."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
    """Cron speedups must not let one revision bless another revision."""
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()

    restored = workflow.index("name: Restore exact-code publish gate")
    verified = workflow.index("name: Verify exact-code publish gate")
    tested = workflow.index("name: Engine tests (publish gates on green)")
    exported = workflow.index("name: Run engines, export snapshot")
    restore_block = workflow[restored:verified]

    assert restored < verified < tested < exported
    assert "seiche-publish-gate-v1-${{ runner.os }}-py312-${{ github.sha }}" in workflow
    assert "restore-keys:" not in restore_block
    assert "[ \"$CACHE_HIT\" = \"true\" ]" in workflow
    assert "= \"$GITHUB_SHA\"" in workflow
    assert "if: steps.publish-gate.outputs.run-full-suite == 'true'" in workflow
    assert 'printf \'%s\\n\' "$GITHUB_SHA"' in workflow[tested:exported]
    assert "-o faulthandler_timeout=300" in workflow[tested:exported]
    assert "--pystack-threshold" not in workflow[tested:exported]
