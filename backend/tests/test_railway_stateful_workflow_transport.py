"""Static authority boundaries for the no-SSH Railway stateful workflows."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CUTOVER = ROOT / ".github" / "workflows" / "railway-stateful-cutover.yml"
RECOVERY = ROOT / ".github" / "workflows" / "railway-stateful-recovery.yml"
SHADOW = ROOT / ".github" / "workflows" / "railway-stateful-shadow.yml"
TELEGRAM = ROOT / ".github" / "workflows" / "railway-telegram.yml"
CADDY = ROOT / "ops" / "Caddyfile"

CONTROL_PATH = "/api/internal/v1/railway-control/commands"
RECOVERY_PATH = "/api/internal/v1/railway-control/recovery"
RESULT_MARKER = "SEICHE_RAILWAY_STATEFUL_RESULT_V1="


def _workflow(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_phase_five_and_six_have_no_native_file_or_shell_transport() -> None:
    for path in (CUTOVER, RECOVERY):
        text = _workflow(path)
        assert re.search(r"\brailway\s+ssh\b", text, re.IGNORECASE) is None
        assert (
            re.search(
                r"\brailway\s+volume\b[^\n]*(?:\\\n[^\n]*)*\bfiles\b",
                text,
                re.IGNORECASE,
            )
            is None
        )
        assert re.search(r"\bsftp\b|\bscp\b", text, re.IGNORECASE) is None


def test_detached_checkout_bundles_advertise_the_exact_head() -> None:
    for path in (SHADOW, CUTOVER, TELEGRAM):
        text = _workflow(path)
        assert 'test "$(git rev-parse \'HEAD^{commit}\')" = "$GITHUB_SHA"' in text
        assert 'git bundle create "$UPLOAD_ROOT/source.bundle" HEAD' in text
        assert (
            'git bundle create "$UPLOAD_ROOT/source.bundle" "$GITHUB_SHA"' not in text
        )
        assert 'git bundle list-heads "$UPLOAD_ROOT/source.bundle"' in text
        assert '"$GITHUB_SHA HEAD"' in text


def test_activation_uses_attested_input_and_signed_origin_command() -> None:
    text = _workflow(CUTOVER)
    assert "authority_fence_base64:" in text
    assert "candidate_run_id:" in text
    assert "gh attestation verify" in text
    assert (
        "--signer-workflow beepboop2025/seiche/.github/workflows/railway-stateful-cutover.yml"
        in text
    )
    assert 'find "$EVIDENCE_ROOT" -mindepth 1 ! -type f' in text
    assert "SEICHE_RAILWAY_ACTIVATION_SIGNING_KEY_PEM" in text
    assert "control.prepare_unsigned_command(" in text
    assert "control.ACTIVATION_OPERATION" in text
    assert f'"$RAILWAY_ORIGIN{CONTROL_PATH}"' in text
    assert '--header "X-Seiche-Edge-Token: $RAILWAY_EDGE_TOKEN"' in text
    assert "actions: write" in text
    assert "railway-stateful-recovery.yml/dispatches" in text
    assert "SUBMISSION_REPLICA_ID" in text
    assert "item.logged_at_unix_ns" in text
    assert RESULT_MARKER in text
    assert "--filter 'SEICHE_RAILWAY_STATEFUL_RESULT_V1='" in text


def test_recovery_uses_masked_bounded_capability_and_closed_member_set() -> None:
    text = _workflow(RECOVERY)
    logical = text.replace("\\\n", " ")
    match = re.search(
        r'download_bearer=\$\(cat "\$bearer_path"\).*?for member in (.*?); do',
        logical,
        re.DOTALL,
    )
    assert match is not None
    members = match.group(1).split()
    assert members == [
        "activation-receipt.json",
        "candidate-receipt.json",
        "shadow-receipt.json",
        "request.json",
        "recovery-receipt.json",
        "seiche.dump",
        "var-lib-seiche.tgz",
        "palimpsest-china.tgz",
        "palimpsest-china-state.json",
        "api-data.tgz",
        "table-counts.txt",
        "deployed-sha.txt",
        "manifest.env",
        "SHA256SUMS",
    ]
    assert "seiche.railway-recovery-export-request.v2" in text
    assert 'echo "::add-mask::$download_bearer"' in text
    assert "Authorization: Bearer $download_bearer" in text
    assert "download_bearer_sha256" in text
    assert "download_expires_at" in text
    assert f'"$RAILWAY_ORIGIN{RECOVERY_PATH}/$request_id/$member"' in text
    assert "SEICHE_RAILWAY_RECOVERY_SIGNING_KEY_PEM" in text
    assert "control.RECOVERY_EXPORT_OPERATION" in text
    assert "control.OFFSITE_ACKNOWLEDGMENT_OPERATION" in text
    assert "extract_latest_recovery_pair" in text
    assert text.count("current_replica()") == 2
    assert "SUBMISSION_REPLICA_ID" in text
    assert "item.logged_at_unix_ns" in text
    assert "activation_candidates" in text
    assert RESULT_MARKER in text
    assert "--filter 'SEICHE_RAILWAY_STATEFUL_RESULT_V1='" in text


def test_public_edge_refuses_all_stateful_control_route_names() -> None:
    text = CADDY.read_text(encoding="utf-8")
    start = text.index("@railway_stateful_control_private")
    end = text.index("# Agent Room", start)
    handler = text[start:end]
    for path in (
        CONTROL_PATH,
        RECOVERY_PATH,
        f"{RECOVERY_PATH}/*",
        "/api/internal/v1/stateful-control",
        "/api/internal/v1/recovery-exports/*",
    ):
        assert path in handler
    assert 'respond "not here" 404' in handler
    assert "reverse_proxy" not in handler
    assert "seiche_stateful_upstream" not in handler
