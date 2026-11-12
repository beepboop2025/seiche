"""Vendored files, copied byte for byte. Never edit them here: change the
source, copy it again, and record the new commit and digest below together.

The record is a Python constant rather than a JSON file beside the copy
because the release artifact verifier admits only reviewed non-Python package
members (tests/test_python_artifact_verifier.py).
"""

PROVENANCE = {
    "rule": (
        "Copied byte for byte. Never edit a vendored file: change the source, "
        "copy it again, and record the new commit and digest here together."
    ),
    "files": {
        "present.py": {
            "repository": "beepboop2025/liquilens-evidence-carrier",
            "branch": "feat/evidence-presenter-20261112",
            "commit": "0ab40e45fabea7038eafe2f35274f928db62a2cd",
            "path": "src/liquilens_evidence/present.py",
            "bytes": 88187,
            "sha256": "082bee97c65c2c76a88872860bbc9bd53f57013f0879392e9f5a24abb5d90560",
            "pull_request": "https://github.com/beepboop2025/liquilens-evidence-carrier/pull/84",
        }
    },
}
