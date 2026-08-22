"""Adversarial tests for the dependency-free release artifact verifier."""

from __future__ import annotations

import gzip
import importlib.util
import io
import struct
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / "ops/release/verify_python_artifacts.py"
SPEC = importlib.util.spec_from_file_location("seiche_artifact_verifier", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFIER
SPEC.loader.exec_module(VERIFIER)


def test_bounded_gzip_rejects_expansion_beyond_budget() -> None:
    compressed = gzip.compress(b"x" * 4096, mtime=0)

    with pytest.raises(VERIFIER.VerificationError, match="expands beyond"):
        VERIFIER._bounded_gzip_decompress(compressed, 1024)


def test_bounded_gzip_rejects_nonzero_terminal_padding_bits() -> None:
    compressed = bytearray(gzip.compress(b"", compresslevel=9, mtime=1_700_000_000))
    compressed[-9] ^= 0x04

    with pytest.raises(VERIFIER.VerificationError, match="not byte-canonical"):
        VERIFIER._bounded_gzip_decompress(bytes(compressed), 1)


def test_metadata_rejects_duplicate_singleton_header() -> None:
    project = {
        "name": "seiche",
        "version": "0.11.0",
        "description": "reviewed",
        "license": "AGPL-3.0-or-later",
        "requires-python": ">=3.11",
        "keywords": ["evidence"],
    }
    metadata = b"Metadata-Version: 2.4\nName: seiche\nName: impostor\n\nREADME"

    with pytest.raises(VERIFIER.VerificationError, match="Name values"):
        VERIFIER._verify_metadata(
            metadata,
            artifact="malicious.whl",
            project=project,
            readme=b"README",
        )


def test_record_rejects_forged_payload_hash() -> None:
    record_name = "seiche-0.11.0.dist-info/RECORD"
    record = f"seiche/payload.py,sha256=forged,7\n{record_name},,\n"
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("seiche/payload.py", b"payload")
        archive.writestr(record_name, record)

    wheel.seek(0)
    with zipfile.ZipFile(wheel) as archive:
        with pytest.raises(VERIFIER.VerificationError, match="hash mismatch"):
            VERIFIER._verify_record(
                archive,
                record_name,
                {"seiche/payload.py", record_name},
            )


def test_sdist_rejects_special_tar_member() -> None:
    member = tarfile.TarInfo("seiche-0.11.0/seiche.py")
    member.type = tarfile.SYMTYPE
    member.linkname = "../../outside"
    member.mtime = 1_700_000_000
    member.mode = 0o644

    with pytest.raises(VERIFIER.VerificationError, match="non-regular"):
        VERIFIER._verify_sdist_member_metadata(
            member,
            artifact="seiche-0.11.0.tar.gz",
            expected_names={member.name},
            seen=set(),
            epoch=member.mtime,
        )


def test_source_contract_rejects_symlinked_required_file(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    target = root / "real.toml"
    target.write_text("[project]\n", encoding="utf-8")
    link = root / "pyproject.toml"
    link.symlink_to(target)

    with pytest.raises(VERIFIER.VerificationError, match="must not be a symlink"):
        VERIFIER._require_regular_source(link, root, label="pyproject")


def _canonical_wheel(payload: bytes) -> bytes:
    wheel = io.BytesIO()
    info = zipfile.ZipInfo("seiche/payload.py")
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.internal_attr = 0
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(info, payload)
    return wheel.getvalue()


@pytest.mark.parametrize("envelope", ["leading", "trailing"])
def test_zip_framing_rejects_bytes_outside_archive(envelope: str) -> None:
    body = _canonical_wheel(b"payload")
    body = b"prefix" + body if envelope == "leading" else body + b"suffix"

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        with pytest.raises(VERIFIER.VerificationError):
            VERIFIER._verify_zip_framing(
                body,
                archive.infolist(),
                artifact="malicious.whl",
                package_members={"seiche/payload.py"},
            )


def test_zip_framing_rejects_hidden_deflate_output() -> None:
    body = bytearray(_canonical_wheel(b"A" * (1024 * 1024)))
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        info = archive.infolist()[0]
        central_offset = archive.start_dir
    one_byte_crc = zlib.crc32(b"A") & 0xFFFFFFFF
    struct.pack_into("<I", body, info.header_offset + 14, one_byte_crc)
    struct.pack_into("<I", body, info.header_offset + 22, 1)
    struct.pack_into("<I", body, central_offset + 16, one_byte_crc)
    struct.pack_into("<I", body, central_offset + 24, 1)

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        with pytest.raises(VERIFIER.VerificationError, match="deflate output"):
            VERIFIER._verify_zip_framing(
                bytes(body),
                archive.infolist(),
                artifact="malicious.whl",
                package_members={"seiche/payload.py"},
            )


def test_zip_framing_rejects_nonzero_terminal_deflate_padding_bits() -> None:
    body = bytearray(_canonical_wheel(b""))
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        info = archive.infolist()[0]
    (
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        name_size,
        extra_size,
    ) = struct.unpack_from("<4s5H3I2H", body, info.header_offset)
    compressed_start = info.header_offset + 30 + name_size + extra_size
    body[compressed_start + info.compress_size - 1] ^= 0x04

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        with pytest.raises(VERIFIER.VerificationError, match="non-canonical deflate"):
            VERIFIER._verify_zip_framing(
                bytes(body),
                archive.infolist(),
                artifact="malicious.whl",
                package_members={"seiche/payload.py"},
            )


@pytest.mark.parametrize(
    ("offset", "format_code", "mutated_value", "message"),
    [
        (4, "<H", (3 << 8) | 21, "ZIP version drift"),
        (36, "<H", 1, "ZIP attributes drift"),
        (38, "<I", 0o120777 << 16, "ZIP attributes drift"),
        (38, "<I", (0o100644 << 16) | 1, "ZIP attributes drift"),
    ],
    ids=["creator-version", "internal-attributes", "symlink-mode", "dos-bits"],
)
def test_zip_framing_rejects_noncanonical_central_attributes(
    offset: int,
    format_code: str,
    mutated_value: int,
    message: str,
) -> None:
    body = bytearray(_canonical_wheel(b"payload"))
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        central_offset = archive.start_dir
    struct.pack_into(format_code, body, central_offset + offset, mutated_value)

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        with pytest.raises(VERIFIER.VerificationError, match=message):
            VERIFIER._verify_zip_framing(
                bytes(body),
                archive.infolist(),
                artifact="malicious.whl",
                package_members={"seiche/payload.py"},
            )


def test_artifact_read_rejects_oversized_file_before_allocation(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "oversized.whl"
    with artifact.open("wb") as stream:
        stream.truncate(VERIFIER.MAX_ARTIFACT_BYTES + 1)

    with pytest.raises(VERIFIER.VerificationError, match="unsafe wheel size"):
        VERIFIER._read_bounded_regular_file(artifact, artifact="wheel")


@pytest.mark.parametrize(("offset", "value"), [(8, 0), (9, 3)], ids=["xfl", "os"])
def test_gzip_header_rejects_noncanonical_xfl_or_os(offset: int, value: int) -> None:
    epoch = 1_700_000_000
    body = bytearray(b"\x1f\x8b\x08\x00" + struct.pack("<I", epoch) + b"\x02\xff")
    body[offset] = value

    with pytest.raises(VERIFIER.VerificationError, match="XFL or OS"):
        VERIFIER._verify_gzip_header(bytes(body), epoch, artifact="sdist")


def _raw_tar(name: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo(name)
        member.mode = 0o644
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        member.mtime = 1_700_000_000
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def test_tar_framing_rejects_nonzero_member_padding() -> None:
    name = "seiche-0.11.0/payload.txt"
    raw_tar = bytearray(_raw_tar(name, b"payload"))
    raw_tar[512 + len(b"payload")] = 1

    with pytest.raises(VERIFIER.VerificationError, match="nonzero TAR data padding"):
        VERIFIER._verify_tar_framing(
            bytes(raw_tar),
            artifact="seiche-0.11.0.tar.gz",
            expected_sizes={name: len(b"payload")},
            epoch=1_700_000_000,
        )


def test_tar_framing_rejects_hidden_gnu_longname_record() -> None:
    name = f"seiche-0.11.0/{'a' * 110}.txt"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.GNU_FORMAT) as archive:
        member = tarfile.TarInfo(name)
        member.mode = 0o644
        member.uid = member.gid = 0
        member.uname = member.gname = ""
        member.mtime = 1_700_000_000
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))

    with pytest.raises(
        VERIFIER.VerificationError,
        match="hidden TAR record|non-canonical member",
    ):
        VERIFIER._verify_tar_framing(
            output.getvalue(),
            artifact="seiche-0.11.0.tar.gz",
            expected_sizes={name: 1},
            epoch=1_700_000_000,
        )
