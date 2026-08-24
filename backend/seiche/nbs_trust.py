"""Minimal, release-pinned Ed25519 trust policy for NBS intake.

This module is intentionally independent of the rest of the Seiche package so
the privileged intake runtime can import it under ``python -I`` without adding
the mutable application checkout to ``sys.path``.  Production verification
uses only keys pinned in the signed release.  A non-hosted installation may
opt into a separate policy by passing an explicit protected directory that
contains ``trusted_operator_keys``.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat


_ED25519_PUBLIC_KEY_RE = re.compile(r"[0-9a-f]{64}")
_ED25519_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_MAX_TRUSTED_OPERATOR_KEYS = 32
_MAX_TRUST_FILE_BYTES = _MAX_TRUSTED_OPERATOR_KEYS * 65

# Rotations are reviewed and added through a signed release.  Historical keys
# remain pinned so already-published evidence remains independently verifiable.
PRODUCTION_TRUSTED_OPERATOR_KEYS = frozenset(
    {"8c2fead17b95e9bed153b7acea346202ebdb987467abcadcdf5799f9ca3e1510"}
)
# Palimpsest China acceptance is an offline-owner ceremony, while the NBS key
# above is provisioned to the live notary host. Never let that online key cross
# this trust boundary. A dedicated offline public key must be added by a later
# signed release before production acceptance can succeed.
PRODUCTION_TRUSTED_PALIMPSEST_CHINA_OPERATOR_KEYS: frozenset[str] = frozenset()


def _open_directory_nofollow(path: Path) -> int:
    """Open an absolute directory through no-follow descriptors."""

    path_text = os.fspath(path)
    if (
        not path_text.startswith("/")
        or os.path.normpath(path_text) != path_text
        or path_text == "/"
    ):
        raise ValueError("operator trust directory is invalid")

    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open("/", flags)
        for component in path.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            visible = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            opened = os.fstat(child)
            if not stat.S_ISDIR(visible.st_mode) or (
                visible.st_dev,
                visible.st_ino,
            ) != (opened.st_dev, opened.st_ino):
                os.close(child)
                raise ValueError("operator trust directory has an unsafe component")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except (OSError, ValueError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("operator trust directory cannot be opened safely") from exc
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _explicit_trusted_operator_keys(
    attest_dir: str | os.PathLike[str],
) -> frozenset[str]:
    """Read one bounded, protected trust policy through its directory fd."""

    if isinstance(attest_dir, (str, os.PathLike)):
        trust_dir = Path(attest_dir)
    else:
        raise ValueError("operator trust directory is invalid")
    if not trust_dir.is_absolute():
        raise ValueError("operator trust directory must be absolute")

    directory_fd = _open_directory_nofollow(trust_dir)
    trust_fd = -1
    try:
        directory_metadata = os.fstat(directory_fd)
        allowed_owners = {0, os.geteuid()}
        if (
            directory_metadata.st_uid not in allowed_owners
            or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        ):
            raise ValueError("operator trust directory is not protected")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        trust_fd = os.open("trusted_operator_keys", flags, dir_fd=directory_fd)
        opened = os.fstat(trust_fd)
        visible = os.stat(
            "trusted_operator_keys", dir_fd=directory_fd, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid not in allowed_owners
            or opened.st_uid != directory_metadata.st_uid
            or stat.S_IMODE(opened.st_mode) & 0o022
            or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)
        ):
            raise ValueError("operator trust policy has unsafe metadata")

        body = bytearray()
        while len(body) <= _MAX_TRUST_FILE_BYTES:
            chunk = os.read(trust_fd, min(4096, _MAX_TRUST_FILE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > _MAX_TRUST_FILE_BYTES:
            raise ValueError("operator trust policy is too large")
        try:
            text = bytes(body).decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("operator trust policy is not ASCII") from exc
        keys = [line for line in text.splitlines() if line]
        if (
            not keys
            or len(keys) > _MAX_TRUSTED_OPERATOR_KEYS
            or len(keys) != len(set(keys))
            or any(_ED25519_PUBLIC_KEY_RE.fullmatch(key) is None for key in keys)
        ):
            raise ValueError("operator trust policy contains malformed keys")
        return frozenset(keys)
    except OSError as exc:
        raise ValueError("operator trust policy cannot be opened safely") from exc
    finally:
        if trust_fd >= 0:
            os.close(trust_fd)
        os.close(directory_fd)


def _verify_trusted_ed25519_signature(
    message: bytes,
    signature_hex: str,
    signer_public_key_hex: str,
    *,
    trusted_keys: frozenset[str],
) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(message, bytes) or not message:
        raise ValueError("signature message must be non-empty bytes")
    if (
        not isinstance(signature_hex, str)
        or _ED25519_SIGNATURE_RE.fullmatch(signature_hex) is None
    ):
        raise ValueError("Ed25519 signature is malformed")
    if (
        not isinstance(signer_public_key_hex, str)
        or _ED25519_PUBLIC_KEY_RE.fullmatch(signer_public_key_hex) is None
    ):
        raise ValueError("Ed25519 signer key is malformed")

    if signer_public_key_hex not in trusted_keys:
        raise ValueError("Ed25519 signer key is not trusted")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(signer_public_key_hex)
        )
        public_key.verify(bytes.fromhex(signature_hex), message)
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise ValueError("Ed25519 signature is invalid") from exc


def verify_trusted_ed25519_signature(
    message: bytes,
    signature_hex: str,
    signer_public_key_hex: str,
    *,
    attest_dir: str | os.PathLike[str] | None = None,
) -> None:
    """Verify an NBS signature under its release-authenticated key policy."""

    trusted_keys = (
        PRODUCTION_TRUSTED_OPERATOR_KEYS
        if attest_dir is None
        else _explicit_trusted_operator_keys(attest_dir)
    )
    _verify_trusted_ed25519_signature(
        message,
        signature_hex,
        signer_public_key_hex,
        trusted_keys=trusted_keys,
    )


def verify_trusted_palimpsest_china_signature(
    message: bytes,
    signature_hex: str,
    signer_public_key_hex: str,
    *,
    attest_dir: str | os.PathLike[str] | None = None,
) -> None:
    """Verify an offline Palimpsest owner signature under a separate policy."""

    trusted_keys = (
        PRODUCTION_TRUSTED_PALIMPSEST_CHINA_OPERATOR_KEYS
        if attest_dir is None
        else _explicit_trusted_operator_keys(attest_dir)
    )
    _verify_trusted_ed25519_signature(
        message,
        signature_hex,
        signer_public_key_hex,
        trusted_keys=trusted_keys,
    )


__all__ = [
    "PRODUCTION_TRUSTED_OPERATOR_KEYS",
    "PRODUCTION_TRUSTED_PALIMPSEST_CHINA_OPERATOR_KEYS",
    "verify_trusted_ed25519_signature",
    "verify_trusted_palimpsest_china_signature",
]
