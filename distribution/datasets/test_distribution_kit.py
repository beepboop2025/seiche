"""Targeted offline test for the rights-reviewed research distribution kit."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE_SCRIPT = Path(__file__).with_name("stage.py")
SPEC = importlib.util.spec_from_file_location("seiche_distribution_stage", STAGE_SCRIPT)
assert SPEC is not None and SPEC.loader is not None
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)


def test_rights_clean_distribution_kit_and_reference_staging(tmp_path):
    report = stage.validate_kit()
    assert report == {
        "status": "valid",
        "publication_status": "draft_not_submitted",
        "doi": None,
        "source": "direct Office of Financial Research only",
        "files": 2,
        "series": 10,
        "records": 11163,
        "observation_data_duplicated": False,
    }

    destination = tmp_path / "stage"
    staged = stage.stage_by_reference(destination)
    assert staged["stage_uses_symlinks"] is True
    for name, source_spec in stage.SOURCE_SPECS.items():
        for link in (
            destination / "huggingface" / "data" / name,
            destination / "kaggle" / name,
        ):
            assert link.is_symlink()
            assert link.resolve() == source_spec["path"].resolve()
    assert (destination / "huggingface" / "README.md").is_symlink()
    assert (destination / "kaggle" / "dataset-metadata.json").is_symlink()

    clients = {
        "python": ROOT / "clients" / "python" / "world_markets.py",
        "r": ROOT / "clients" / "r" / "world_markets.R",
        "javascript": ROOT / "clients" / "javascript" / "world-markets.mjs",
    }
    for language, path in clients.items():
        text = path.read_text(encoding="utf-8")
        assert "/api/v2/world-markets" in text, language
        assert "seiche.world-markets.v1" in text, language
        assert "curated_partial_non_exhaustive" in text, language
        assert "citation" in text and "clock" in text.lower(), language
        assert "2000000" in text or "2_000_000" in text, language
    compile(clients["python"].read_text(encoding="utf-8"), str(clients["python"]), "exec")


def test_croissant_citation_targets_a_real_immutable_source_tree():
    """A commit-looking URL is not provenance when its path is a 404."""
    subprocess.run(
        [
            "git",
            "cat-file",
            "-e",
            f"{stage.EXPECTED_COMMIT}:integrations/datacommons",
        ],
        cwd=ROOT,
        check=True,
    )
