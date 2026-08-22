"""Targeted offline test for the rights-reviewed research distribution kit."""

from __future__ import annotations

import importlib.util
import json
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

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
        "platform_upload_payloads": "publication_ready",
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
    compile(
        clients["python"].read_text(encoding="utf-8"), str(clients["python"]), "exec"
    )


def test_source_revision_targets_a_real_immutable_tree():
    """A commit-looking source URL is not provenance when its path is a 404."""
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


def test_hugging_face_card_is_native_schema_safe_and_publication_ready():
    card = (stage.KIT_ROOT / "huggingface" / "README.md").read_text(encoding="utf-8")
    frontmatter = card.split("---\n", maxsplit=2)[1]
    match = re.search(r"^license_name:\s*(.*?)\s*$", frontmatter, re.MULTILINE)
    assert match is not None
    assert match.group(1) == stage.HUGGING_FACE_LICENSE_NAME
    assert re.fullmatch(r"[a-z0-9-.]+", match.group(1))
    for stale_copy in (
        "proposed dataset",
        "staged by reference",
        "draft, not submitted",
    ):
        assert stale_copy not in card.lower()
    link_targets = stage.MARKDOWN_LINK_TARGET_PATTERN.findall(card)
    assert link_targets
    assert all(target.startswith(("https://", "#")) for target in link_targets)
    assert (
        "https://github.com/beepboop2025/seiche/blob/v0.11.0/"
        "integrations/datacommons/RIGHTS_AND_SOURCES.md"
    ) in link_targets


def _optional_live_receipt_cell() -> str:
    notebook_path = ROOT / "notebooks" / "seiche_direct_ofr_research.ipynb"
    notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
    matches = [
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
        and "world-markets response exceeds" in "".join(cell["source"])
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("name resolution failed"),
        ConnectionError("connection refused"),
        TimeoutError("request timed out"),
        ssl.SSLError("TLS negotiation failed"),
    ],
)
def test_optional_live_receipt_degrades_only_transport_failures(monkeypatch, failure):
    def unavailable(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    namespace = {"json": json, "urllib": urllib}
    exec(
        compile(_optional_live_receipt_cell(), "<optional-live-receipt>", "exec"),
        namespace,
    )

    assert namespace["live_receipt"] == {
        "status": "unavailable",
        "transport_error": type(failure).__name__,
        "reason": "No live evidence was substituted.",
    }


def test_optional_live_receipt_preserves_http_failure_details(monkeypatch):
    failure = urllib.error.HTTPError(
        url="https://api.seiche.info/api/v2/world-markets",
        code=503,
        msg="Service Unavailable",
        hdrs={"Retry-After": "60"},
        fp=None,
    )

    def unavailable(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    namespace = {"json": json, "urllib": urllib}
    exec(
        compile(_optional_live_receipt_cell(), "<optional-live-receipt>", "exec"),
        namespace,
    )

    assert namespace["live_receipt"] == {
        "status": "unavailable",
        "http_status": 503,
        "retry_after": "60",
        "reason": "No live evidence was substituted.",
    }


@pytest.mark.parametrize(
    ("payload", "error_type", "message"),
    [
        (b"{", json.JSONDecodeError, None),
        (
            b'{"schema":"wrong","selection":"sources"}',
            ValueError,
            "unexpected world-markets contract",
        ),
    ],
)
def test_optional_live_receipt_does_not_swallow_contract_errors(
    monkeypatch, payload, error_type, message
):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return payload

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    namespace = {"json": json, "urllib": urllib}

    with pytest.raises(error_type, match=message):
        exec(
            compile(_optional_live_receipt_cell(), "<optional-live-receipt>", "exec"),
            namespace,
        )
