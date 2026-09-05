"""Publisher attestations must not mutate the verified two-file inventory."""

import os
from pathlib import Path
import subprocess
import textwrap


def test_attestation_sidecars_leave_verified_distributions_unchanged(
    tmp_path: Path,
) -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/publish-openbb.yml"
    ).read_text()
    stage = workflow.split(
        "      - name: Stage upload copies without mutating the verified inventory\n", 1
    )[1].split("      - name:", 1)[0]
    script = textwrap.dedent(stage.split("        run: |\n", 1)[1])
    wheel = "openbb_seiche-0.1.0-py3-none-any.whl"
    sdist = "openbb_seiche-0.1.0.tar.gz"
    verified = tmp_path / "openbb-verified"
    verified.mkdir()
    expected = {wheel: b"verified wheel bytes", sdist: b"verified source bytes"}
    for name, body in expected.items():
        (verified / name).write_bytes(body)
    subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        check=True,
        env={**os.environ, "EXPECTED_WHEEL_NAME": wheel, "EXPECTED_SDIST_NAME": sdist},
    )
    upload = tmp_path / "openbb-upload"
    for name, body in expected.items():
        assert (upload / name).read_bytes() == body
        (upload / f"{name}.publish.attestation").write_bytes(b"publisher provenance")
        (upload / name).write_bytes(b"action workspace mutation")
    assert {path.name: path.read_bytes() for path in verified.iterdir()} == expected
    assert "packages-dir: openbb-upload" in workflow
    assert 'dist = Path("openbb-verified")' in workflow
