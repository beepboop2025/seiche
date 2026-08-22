"""Adversarial tests for the reusable OpenBB artifact verifier."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import gzip
import hashlib
import importlib.util
import io
from pathlib import Path
import stat
import struct
import sys
import tarfile
import warnings
import zipfile
import zlib

import pytest


VERIFIER_PATH = Path(__file__).parents[1] / "verify_artifacts.py"
SPEC = importlib.util.spec_from_file_location("openbb_artifact_verifier", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


EPOCH = 1_787_351_583
README = b"# OpenBB artifact fixture\n"
VALID_PYPROJECT = b"""[project]
name = "openbb-seiche"
version = "0.1.0"
description = "Seiche funding-liquidity and world-markets evidence provider for OpenBB"
readme = "README.md"
license = "AGPL-3.0-or-later"
license-files = ["LICENSE"]
authors = [
    { name = "Mrinal", email = "beepboop2025@users.noreply.github.com" },
]
maintainers = [
    { name = "Seiche maintainers", email = "beepboop2025@users.noreply.github.com" },
]
keywords = ["openbb", "funding-liquidity", "money-markets", "financial-data"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Financial and Insurance Industry",
    "Intended Audience :: Science/Research",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Topic :: Office/Business :: Financial",
    "Typing :: Typed",
]
requires-python = ">=3.10,<4"
dependencies = [
    "httpx>=0.27,<1",
    "openbb-core>=1.6.10,<2.0.0",
]

[project.urls]
Homepage = "https://seiche.info"
Documentation = "https://seiche.info/developers"
Repository = "https://github.com/beepboop2025/seiche"
Issues = "https://github.com/beepboop2025/seiche/issues"

[project.entry-points."openbb_provider_extension"]
seiche = "openbb_seiche:seiche_provider"

[project.entry-points."openbb_core_extension"]
seiche = "openbb_seiche.router.seiche_router:router"

[tool.poetry]
packages = [{ include = "openbb_seiche" }]

[build-system]
requires = ["poetry-core==2.4.1"]
build-backend = "poetry.core.masonry.api"
"""


@dataclass(frozen=True)
class ArtifactFixture:
    source: Path
    dist: Path


def _metadata() -> bytes:
    headers = "\n".join(
        f"{name}: {value}" for name, value in verifier.EXPECTED_METADATA_HEADERS
    ).encode()
    return headers + b"\n\n" + README + b"\n"


def _record_hash(payload: bytes) -> str:
    digest = hashlib.sha256(payload).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _wheel_payloads(source: Path, metadata: bytes) -> dict[str, bytes]:
    payloads = {name: (source / name).read_bytes() for name in verifier.PACKAGE_FILES}
    payloads.update(
        {
            verifier.METADATA_PATH: metadata,
            verifier.WHEEL_PATH: verifier.EXPECTED_WHEEL,
            verifier.ENTRY_POINTS_PATH: verifier.EXPECTED_ENTRY_POINTS,
            verifier.WHEEL_LICENSE_PATH: (source / "LICENSE").read_bytes(),
        }
    )
    rows = [
        f"{name},{_record_hash(payloads[name])},{len(payloads[name])}\n"
        for name in verifier.WHEEL_MEMBERS
        if name != verifier.RECORD_PATH
    ]
    rows.append(f"{verifier.RECORD_PATH},,\n")
    payloads[verifier.RECORD_PATH] = "".join(rows).encode()
    return payloads


def _write_wheel(
    fixture: ArtifactFixture,
    *,
    metadata: bytes | None = None,
    record: bytes | None = None,
    extra_member: tuple[str, bytes] | None = None,
    duplicate: str | None = None,
    mode_overrides: dict[str, int] | None = None,
    payload_overrides: dict[str, bytes] | None = None,
    epoch_offset: int = 0,
) -> None:
    payloads = _wheel_payloads(fixture.source, metadata or _metadata())
    if payload_overrides:
        payloads.update(payload_overrides)
        if record is None:
            rows = [
                f"{name},{_record_hash(payloads[name])},{len(payloads[name])}\n"
                for name in verifier.WHEEL_MEMBERS
                if name != verifier.RECORD_PATH
            ]
            rows.append(f"{verifier.RECORD_PATH},,\n")
            payloads[verifier.RECORD_PATH] = "".join(rows).encode()
    if record is not None:
        payloads[verifier.RECORD_PATH] = record
    names = list(verifier.WHEEL_MEMBERS)
    if extra_member:
        name, payload = extra_member
        names.append(name)
        payloads[name] = payload
    timestamp = verifier._wheel_timestamp(EPOCH + epoch_offset)
    modes = mode_overrides or {}
    path = fixture.dist / verifier.WHEEL_FILENAME
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.create_system = 3
            default_mode = 0o644 if name == verifier.RECORD_PATH else 0o100644
            info.external_attr = modes.get(name, default_mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payloads[name], compresslevel=9)
        if duplicate:
            info = zipfile.ZipInfo(duplicate, date_time=timestamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(info, payloads[duplicate], compresslevel=9)


def _inject_hidden_wheel_output(fixture: ArtifactFixture, name: str) -> None:
    path = fixture.dist / verifier.WHEEL_FILENAME
    original = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        infos = archive.infolist()
        target = archive.getinfo(name)

    local = verifier.ZIP_LOCAL_HEADER.unpack_from(original, target.header_offset)
    name_length, extra_length = local[-2:]
    data_start = (
        target.header_offset
        + verifier.ZIP_LOCAL_HEADER.size
        + name_length
        + extra_length
    )
    data_end = data_start + target.compress_size
    payload = (fixture.source / name).read_bytes() + (
        b"A" * (verifier.MAX_EXPANDED_BYTES + 1)
    )
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    delta = len(compressed) - target.compress_size
    mutated = bytearray(original[:data_start] + compressed + original[data_end:])

    struct.pack_into("<I", mutated, target.header_offset + 18, len(compressed))
    old_end_offset = len(original) - verifier.ZIP_END_RECORD.size
    old_directory_offset = struct.unpack_from("<I", original, old_end_offset + 16)[0]
    new_directory_offset = old_directory_offset + delta
    central_offset = new_directory_offset
    for info in infos:
        central = verifier.ZIP_CENTRAL_HEADER.unpack_from(mutated, central_offset)
        name_length, extra_length, comment_length = central[10:13]
        member_name = bytes(
            mutated[
                central_offset + verifier.ZIP_CENTRAL_HEADER.size : central_offset
                + verifier.ZIP_CENTRAL_HEADER.size
                + name_length
            ]
        ).decode("ascii")
        if member_name == name:
            struct.pack_into("<I", mutated, central_offset + 20, len(compressed))
        if info.header_offset > target.header_offset:
            struct.pack_into(
                "<I", mutated, central_offset + 42, info.header_offset + delta
            )
        central_offset += (
            verifier.ZIP_CENTRAL_HEADER.size
            + name_length
            + extra_length
            + comment_length
        )

    new_end_offset = old_end_offset + delta
    struct.pack_into("<I", mutated, new_end_offset + 16, new_directory_offset)
    path.write_bytes(mutated)


def _sdist_payloads(source: Path, metadata: bytes) -> dict[str, bytes]:
    payloads = {
        f"{verifier.SDIST_ROOT}/LICENSE": (source / "LICENSE").read_bytes(),
        f"{verifier.SDIST_ROOT}/README.md": (source / "README.md").read_bytes(),
    }
    payloads.update(
        {
            f"{verifier.SDIST_ROOT}/{name}": (source / name).read_bytes()
            for name in verifier.PACKAGE_FILES
        }
    )
    payloads[f"{verifier.SDIST_ROOT}/pyproject.toml"] = (
        source / "pyproject.toml"
    ).read_bytes()
    payloads[f"{verifier.SDIST_ROOT}/PKG-INFO"] = metadata
    return payloads


def _write_sdist(
    fixture: ArtifactFixture,
    *,
    metadata: bytes | None = None,
    extra_member: tuple[str, bytes] | None = None,
    duplicate: str | None = None,
    type_overrides: dict[str, bytes] | None = None,
    mode_overrides: dict[str, int] | None = None,
    owner_overrides: dict[str, tuple[int, int, str, str]] | None = None,
    epoch_offset: int = 0,
) -> None:
    payloads = _sdist_payloads(fixture.source, metadata or _metadata())
    names = list(verifier.SDIST_MEMBERS)
    if extra_member:
        name, payload = extra_member
        names.append(name)
        payloads[name] = payload
    if duplicate:
        names.append(duplicate)

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in names:
            payload = payloads[name]
            member = tarfile.TarInfo(name)
            member.mode = (mode_overrides or {}).get(name, 0o644)
            member.mtime = EPOCH + epoch_offset
            owner = (owner_overrides or {}).get(name, (0, 0, "", ""))
            member.uid, member.gid, member.uname, member.gname = owner
            member.type = (type_overrides or {}).get(name, tarfile.REGTYPE)
            if member.type == tarfile.REGTYPE:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            else:
                member.size = 0
                member.linkname = "target"
                archive.addfile(member)

    path = fixture.dist / verifier.SDIST_FILENAME
    with path.open("wb") as output:
        with gzip.GzipFile(
            filename=f"{verifier.SDIST_ROOT}.tar",
            mode="wb",
            compresslevel=9,
            fileobj=output,
            mtime=EPOCH + epoch_offset,
        ) as compressed:
            compressed.write(raw.getvalue())


def _write_raw_sdist(fixture: ArtifactFixture, raw_tar: bytes) -> None:
    path = fixture.dist / verifier.SDIST_FILENAME
    with path.open("wb") as output:
        with gzip.GzipFile(
            filename=f"{verifier.SDIST_ROOT}.tar",
            mode="wb",
            compresslevel=9,
            fileobj=output,
            mtime=EPOCH,
        ) as compressed:
            compressed.write(raw_tar)


def _make_fixture(tmp_path: Path) -> ArtifactFixture:
    source = tmp_path / "source"
    dist = tmp_path / "dist"
    source.mkdir()
    dist.mkdir()
    (source / "README.md").write_bytes(README)
    (source / "pyproject.toml").write_bytes(VALID_PYPROJECT)
    project_license = Path(__file__).parents[1] / "LICENSE"
    (source / "LICENSE").write_bytes(project_license.read_bytes())
    for name in verifier.PACKAGE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"\n" if name.endswith("py.typed") else f"# {name}\n".encode()
        path.write_bytes(payload)
    fixture = ArtifactFixture(source, dist)
    _write_wheel(fixture)
    _write_sdist(fixture)
    return fixture


def test_valid_artifacts_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    fixture = _make_fixture(tmp_path)
    receipt = verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)
    assert len(receipt.wheel_sha256) == 64
    assert len(receipt.sdist_sha256) == 64

    assert (
        verifier.main(
            [
                "--dist",
                str(fixture.dist),
                "--source",
                str(fixture.source),
                "--epoch",
                str(EPOCH),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "verified openbb_seiche-0.1.0-py3-none-any.whl sha256:" in output.out
    assert output.err == ""


@pytest.mark.parametrize("mutation", ["duplicate", "unsafe"])
def test_rejects_duplicate_and_unsafe_wheel_members(tmp_path: Path, mutation: str):
    fixture = _make_fixture(tmp_path)
    if mutation == "duplicate":
        _write_wheel(fixture, duplicate=verifier.PACKAGE_FILES[0])
        message = "duplicate members"
    else:
        _write_wheel(fixture, extra_member=("../escape", b"bad"))
        message = "unsafe members"
    with pytest.raises(verifier.ArtifactVerificationError, match=message):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_hidden_zip_expansion(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    _inject_hidden_wheel_output(fixture, "openbb_seiche/py.typed")
    with pytest.raises(verifier.ArtifactVerificationError, match="expands beyond"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


@pytest.mark.parametrize(
    "mutation",
    ["leading", "trailing", "local-method", "local-timestamp", "local-size"],
)
def test_rejects_noncanonical_zip_framing(tmp_path: Path, mutation: str):
    fixture = _make_fixture(tmp_path)
    path = fixture.dist / verifier.WHEEL_FILENAME
    artifact = bytearray(path.read_bytes())
    if mutation == "leading":
        artifact = bytearray(b"prefix") + artifact
    elif mutation == "trailing":
        artifact.extend(b"suffix")
    elif mutation == "local-method":
        struct.pack_into("<H", artifact, 8, zipfile.ZIP_STORED)
    elif mutation == "local-timestamp":
        struct.pack_into("<H", artifact, 10, 0)
    else:
        struct.pack_into("<I", artifact, 22, 0)
    path.write_bytes(artifact)
    with pytest.raises(verifier.ArtifactVerificationError):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


@pytest.mark.parametrize("mutation", ["duplicate", "unsafe", "special"])
def test_rejects_duplicate_unsafe_and_special_sdist_members(
    tmp_path: Path, mutation: str
):
    fixture = _make_fixture(tmp_path)
    first = verifier.SDIST_MEMBERS[0]
    if mutation == "duplicate":
        _write_sdist(fixture, duplicate=first)
        message = "duplicate members"
    elif mutation == "unsafe":
        _write_sdist(fixture, extra_member=("../escape", b"bad"))
        message = "unsafe members"
    else:
        _write_sdist(fixture, type_overrides={first: tarfile.SYMTYPE})
        message = "special members"
    with pytest.raises(verifier.ArtifactVerificationError, match=message):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("mode", "mode mismatch"),
        ("owner", "ownership mismatch"),
        ("epoch", "gzip epoch"),
    ],
)
def test_rejects_sdist_mode_owner_and_epoch_drift(
    tmp_path: Path, mutation: str, message: str
):
    fixture = _make_fixture(tmp_path)
    first = verifier.SDIST_MEMBERS[0]
    if mutation == "mode":
        _write_sdist(fixture, mode_overrides={first: 0o600})
    elif mutation == "owner":
        _write_sdist(fixture, owner_overrides={first: (501, 20, "user", "staff")})
    else:
        _write_sdist(fixture, epoch_offset=2)
    with pytest.raises(verifier.ArtifactVerificationError, match=message):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_wheel_special_mode_and_epoch_drift(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    first = verifier.PACKAGE_FILES[0]
    _write_wheel(fixture, mode_overrides={first: stat.S_IFLNK | 0o777})
    with pytest.raises(verifier.ArtifactVerificationError, match="mode mismatch"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)

    _write_wheel(fixture, epoch_offset=2)
    with pytest.raises(verifier.ArtifactVerificationError, match="epoch mismatch"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_bounded_gzip_expansion_bomb(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    path = fixture.dist / verifier.SDIST_FILENAME
    with path.open("wb") as output:
        with gzip.GzipFile(
            filename=f"{verifier.SDIST_ROOT}.tar",
            mode="wb",
            compresslevel=9,
            fileobj=output,
            mtime=EPOCH,
        ) as compressed:
            compressed.write(b"A" * (verifier.MAX_EXPANDED_BYTES + 1))
    with pytest.raises(verifier.ArtifactVerificationError, match="expanded size"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_noncanonical_sdist_zero_padding(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    path = fixture.dist / verifier.SDIST_FILENAME
    raw_tar = gzip.decompress(path.read_bytes()) + (b"\0" * tarfile.RECORDSIZE)
    with path.open("wb") as output:
        with gzip.GzipFile(
            filename=f"{verifier.SDIST_ROOT}.tar",
            mode="wb",
            compresslevel=9,
            fileobj=output,
            mtime=EPOCH,
        ) as compressed:
            compressed.write(raw_tar)
    with pytest.raises(
        verifier.ArtifactVerificationError, match="non-canonical trailer"
    ):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_hidden_gnu_tar_record(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    path = fixture.dist / verifier.SDIST_FILENAME
    raw_tar = gzip.decompress(path.read_bytes())
    long_name = verifier.SDIST_MEMBERS[0].encode("ascii") + b"\0"
    pseudo = tarfile.TarInfo("././@LongLink")
    pseudo.type = tarfile.GNUTYPE_LONGNAME
    pseudo.mode = 0o644
    pseudo.mtime = EPOCH
    pseudo.size = len(long_name)
    prefix = pseudo.tobuf(format=tarfile.GNU_FORMAT) + long_name
    prefix += b"\0" * (-len(prefix) % tarfile.BLOCKSIZE)
    mutated = prefix + raw_tar
    mutated += b"\0" * (-len(mutated) % tarfile.RECORDSIZE)
    _write_raw_sdist(fixture, mutated)
    with pytest.raises(verifier.ArtifactVerificationError, match="special physical"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_nonzero_tar_member_padding(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    path = fixture.dist / verifier.SDIST_FILENAME
    raw_tar = bytearray(gzip.decompress(path.read_bytes()))
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        first = archive.getmembers()[0]
    padding_offset = first.offset_data + first.size
    assert padding_offset % tarfile.BLOCKSIZE
    raw_tar[padding_offset] = 1
    _write_raw_sdist(fixture, bytes(raw_tar))
    with pytest.raises(verifier.ArtifactVerificationError, match="padding"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_record_tampering(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    payloads = _wheel_payloads(fixture.source, _metadata())
    record = payloads[verifier.RECORD_PATH].replace(b"sha256=", b"sha256=X", 1)
    _write_wheel(fixture, record=record)
    with pytest.raises(verifier.ArtifactVerificationError, match="digest or size"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_noncanonical_record_csv_quoting(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    payloads = _wheel_payloads(fixture.source, _metadata())
    first = verifier.WHEEL_MEMBERS[0].encode("ascii")
    record = payloads[verifier.RECORD_PATH].replace(first, b'"' + first + b'"', 1)
    _write_wheel(fixture, record=record)
    with pytest.raises(verifier.ArtifactVerificationError, match="serialization"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_metadata_divergence_and_wrong_headers(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    _write_sdist(fixture, metadata=_metadata() + b"unexpected")
    with pytest.raises(verifier.ArtifactVerificationError, match="byte-identical"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)

    wrong = _metadata().replace(
        b"License-Expression: AGPL-3.0-or-later",
        b"License-Expression: MIT",
    )
    _write_wheel(fixture, metadata=wrong)
    _write_sdist(fixture, metadata=wrong)
    with pytest.raises(verifier.ArtifactVerificationError, match="metadata headers"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_artifact_content_not_bound_to_source(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    package_file = fixture.source / verifier.PACKAGE_FILES[0]
    package_file.write_bytes(b"# changed after the build\n")
    with pytest.raises(verifier.ArtifactVerificationError, match="differs from source"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)


def test_rejects_legacy_poetry_metadata_and_extra_dist_files(tmp_path: Path):
    fixture = _make_fixture(tmp_path)
    pyproject = fixture.source / "pyproject.toml"
    pyproject.write_bytes(
        VALID_PYPROJECT + b'\n[tool.poetry.dependencies]\nhttpx = "*"\n'
    )
    with pytest.raises(verifier.ArtifactVerificationError, match="legacy Poetry"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)

    pyproject.write_bytes(VALID_PYPROJECT)
    (fixture.dist / "unexpected.txt").write_text("unexpected")
    with pytest.raises(verifier.ArtifactVerificationError, match="inventory mismatch"):
        verifier.verify_artifacts(fixture.dist, fixture.source, EPOCH)
