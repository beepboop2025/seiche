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
            "commit": "a274864c51276bef48d4fab835126e69f13173df",
            "path": "src/liquilens_evidence/present.py",
            "bytes": 87764,
            "sha256": "fdef09fd92635e780d2b17ea3641fb06f598e34a120101cff12553443bc19000",
            "pull_request": "https://github.com/beepboop2025/liquilens-evidence-carrier/pull/84",
        }
    },
}
