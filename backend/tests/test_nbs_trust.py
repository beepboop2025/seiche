from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from seiche import attest, nbs_trust


def _explicit_policy(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_hex = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    trust = tmp_path / "trust"
    trust.mkdir(mode=0o700)
    trust.chmod(0o700)
    policy = trust / "trusted_operator_keys"
    policy.write_text(f"{public_hex}\n", encoding="ascii")
    policy.chmod(0o600)
    return trust, private_key, public_hex


def test_attestation_and_sealed_intake_share_one_release_key_policy() -> None:
    assert (
        attest.PRODUCTION_TRUSTED_OPERATOR_KEYS
        is nbs_trust.PRODUCTION_TRUSTED_OPERATOR_KEYS
    )
    assert "seiche.attest" not in Path(nbs_trust.__file__).read_text(encoding="utf-8")


def test_explicit_protected_policy_verifies_ed25519(tmp_path: Path) -> None:
    trust, private_key, public_hex = _explicit_policy(tmp_path)
    message = b"seiche-nbs-explicit-policy-test"
    signature = private_key.sign(message).hex()

    nbs_trust.verify_trusted_ed25519_signature(
        message,
        signature,
        public_hex,
        attest_dir=trust,
    )
    with pytest.raises(ValueError, match="invalid"):
        nbs_trust.verify_trusted_ed25519_signature(
            message + b"-changed",
            signature,
            public_hex,
            attest_dir=trust,
        )


def test_ambient_trust_environment_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust, private_key, public_hex = _explicit_policy(tmp_path)
    message = b"seiche-nbs-no-ambient-policy"
    monkeypatch.setenv("SEICHE_ATTEST_DIR", str(trust))
    monkeypatch.setenv("SEICHE_TRUSTED_OPERATOR_KEYS", public_hex)

    with pytest.raises(ValueError, match="not trusted"):
        nbs_trust.verify_trusted_ed25519_signature(
            message,
            private_key.sign(message).hex(),
            public_hex,
        )


def test_explicit_policy_rejects_a_symlinked_path_component(tmp_path: Path) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    trust, private_key, public_hex = _explicit_policy(real_parent)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    message = b"seiche-nbs-symlink-policy"

    with pytest.raises(ValueError, match="cannot be opened safely"):
        nbs_trust.verify_trusted_ed25519_signature(
            message,
            private_key.sign(message).hex(),
            public_hex,
            attest_dir=linked_parent / trust.name,
        )
