#!/usr/bin/env python3
"""Fail-closed verification for the published OpenBB provider artifacts."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tarfile
import tomllib
import zipfile
import zlib


PROJECT_NAME = "openbb-seiche"
NORMALIZED_NAME = "openbb_seiche"
VERSION = "0.1.0"
SDIST_ROOT = f"{NORMALIZED_NAME}-{VERSION}"
DIST_INFO = f"{NORMALIZED_NAME}-{VERSION}.dist-info"
WHEEL_FILENAME = f"{NORMALIZED_NAME}-{VERSION}-py3-none-any.whl"
SDIST_FILENAME = f"{NORMALIZED_NAME}-{VERSION}.tar.gz"

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_MEMBERS = 64

ZIP_LOCAL_HEADER = struct.Struct("<4s5H3I2H")
ZIP_CENTRAL_HEADER = struct.Struct("<4s6H3I5H2I")
ZIP_END_RECORD = struct.Struct("<4s4H2IH")

LICENSE_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"

PACKAGE_FILES = (
    "openbb_seiche/__init__.py",
    "openbb_seiche/models/__init__.py",
    "openbb_seiche/models/_client.py",
    "openbb_seiche/models/data_health.py",
    "openbb_seiche/models/funding_stress.py",
    "openbb_seiche/models/world_markets.py",
    "openbb_seiche/py.typed",
    "openbb_seiche/router/__init__.py",
    "openbb_seiche/router/seiche_router.py",
)

METADATA_PATH = f"{DIST_INFO}/METADATA"
WHEEL_PATH = f"{DIST_INFO}/WHEEL"
ENTRY_POINTS_PATH = f"{DIST_INFO}/entry_points.txt"
WHEEL_LICENSE_PATH = f"{DIST_INFO}/licenses/LICENSE"
RECORD_PATH = f"{DIST_INFO}/RECORD"

WHEEL_MEMBERS = (
    *PACKAGE_FILES,
    METADATA_PATH,
    WHEEL_PATH,
    ENTRY_POINTS_PATH,
    WHEEL_LICENSE_PATH,
    RECORD_PATH,
)

SDIST_RELATIVE_MEMBERS = (
    "LICENSE",
    "README.md",
    *PACKAGE_FILES,
    "pyproject.toml",
    "PKG-INFO",
)
SDIST_MEMBERS = tuple(f"{SDIST_ROOT}/{name}" for name in SDIST_RELATIVE_MEMBERS)

CLASSIFIERS = (
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
)

EXPECTED_METADATA_HEADERS = (
    ("Metadata-Version", "2.4"),
    ("Name", PROJECT_NAME),
    ("Version", VERSION),
    (
        "Summary",
        "Seiche funding-liquidity and world-markets evidence provider for OpenBB",
    ),
    ("License-Expression", "AGPL-3.0-or-later"),
    ("License-File", "LICENSE"),
    ("Keywords", "openbb,funding-liquidity,money-markets,financial-data"),
    ("Author", "Mrinal"),
    ("Author-email", "beepboop2025@users.noreply.github.com"),
    ("Maintainer", "Seiche maintainers"),
    ("Maintainer-email", "beepboop2025@users.noreply.github.com"),
    ("Requires-Python", ">=3.10,<4"),
    *(("Classifier", value) for value in CLASSIFIERS),
    ("Requires-Dist", "httpx (>=0.27,<1)"),
    ("Requires-Dist", "openbb-core (>=1.6.10,<2.0.0)"),
    ("Project-URL", "Documentation, https://seiche.info/developers"),
    ("Project-URL", "Homepage, https://seiche.info"),
    ("Project-URL", "Issues, https://github.com/beepboop2025/seiche/issues"),
    ("Project-URL", "Repository, https://github.com/beepboop2025/seiche"),
    ("Description-Content-Type", "text/markdown"),
)

EXPECTED_WHEEL = b"""Wheel-Version: 1.0
Generator: poetry-core 2.4.1
Root-Is-Purelib: true
Tag: py3-none-any
"""

EXPECTED_ENTRY_POINTS = b"""[openbb_core_extension]
seiche=openbb_seiche.router.seiche_router:router

[openbb_provider_extension]
seiche=openbb_seiche:seiche_provider

"""

EXPECTED_PROJECT = {
    "name": PROJECT_NAME,
    "version": VERSION,
    "description": (
        "Seiche funding-liquidity and world-markets evidence provider for OpenBB"
    ),
    "readme": "README.md",
    "license": "AGPL-3.0-or-later",
    "license-files": ["LICENSE"],
    "authors": [{"name": "Mrinal", "email": "beepboop2025@users.noreply.github.com"}],
    "maintainers": [
        {
            "name": "Seiche maintainers",
            "email": "beepboop2025@users.noreply.github.com",
        }
    ],
    "keywords": [
        "openbb",
        "funding-liquidity",
        "money-markets",
        "financial-data",
    ],
    "classifiers": list(CLASSIFIERS),
    "requires-python": ">=3.10,<4",
    "dependencies": ["httpx>=0.27,<1", "openbb-core>=1.6.10,<2.0.0"],
    "urls": {
        "Homepage": "https://seiche.info",
        "Documentation": "https://seiche.info/developers",
        "Repository": "https://github.com/beepboop2025/seiche",
        "Issues": "https://github.com/beepboop2025/seiche/issues",
    },
    "entry-points": {
        "openbb_provider_extension": {"seiche": "openbb_seiche:seiche_provider"},
        "openbb_core_extension": {
            "seiche": "openbb_seiche.router.seiche_router:router"
        },
    },
}


class ArtifactVerificationError(ValueError):
    """Raised when an artifact violates the publication contract."""


@dataclass(frozen=True)
class VerificationReceipt:
    """Immutable hashes returned after successful verification."""

    wheel_sha256: str
    wheel_size: int
    sdist_sha256: str
    sdist_size: int


@dataclass(frozen=True)
class SourcePayloads:
    """Bounded source bytes used as the artifact authority."""

    package: dict[str, bytes]
    license: bytes
    readme: bytes
    pyproject: bytes


def _reject(message: str) -> None:
    raise ArtifactVerificationError(message)


def _require_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _reject(f"{label} does not exist: {path}")
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        _reject(f"{label} is not a real directory: {path}")


def _read_regular_file(path: Path, label: str, limit: int) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _reject(f"missing {label}: {path}")
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        _reject(f"{label} is not a regular file: {path}")
    if metadata.st_size > limit:
        _reject(f"{label} exceeds {limit} bytes: {path}")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit or len(payload) != metadata.st_size:
        _reject(f"{label} changed or exceeded its size bound: {path}")
    return payload


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and str(path) == name


def _validate_source_pyproject(payload: bytes) -> None:
    try:
        document = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        _reject(f"source pyproject.toml is invalid: {exc}")

    if document.get("project") != EXPECTED_PROJECT:
        _reject("source pyproject.toml does not match the exact PEP 621 contract")
    if document.get("build-system") != {
        "requires": ["poetry-core==2.4.1"],
        "build-backend": "poetry.core.masonry.api",
    }:
        _reject("source build backend is not exactly pinned to poetry-core 2.4.1")

    poetry = document.get("tool", {}).get("poetry", {})
    if poetry.get("packages") != [{"include": "openbb_seiche"}]:
        _reject("source Poetry package selection is not exact")
    legacy_fields = {
        "name",
        "version",
        "description",
        "authors",
        "maintainers",
        "license",
        "readme",
        "keywords",
        "classifiers",
        "urls",
        "dependencies",
        "plugins",
    }
    present_legacy_fields = sorted(legacy_fields.intersection(poetry))
    if present_legacy_fields:
        _reject(f"legacy Poetry metadata remains: {present_legacy_fields}")


def _load_source(source: Path) -> SourcePayloads:
    _require_directory(source, "source directory")
    package_root = source / NORMALIZED_NAME
    _require_directory(package_root, "source package directory")

    observed: set[str] = set()
    for path in package_root.rglob("*"):
        if path.is_symlink():
            _reject(f"source package contains a symlink: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(source).as_posix()
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if not path.is_file():
            _reject(f"source package contains a special file: {path}")
        observed.add(relative)
    if observed != set(PACKAGE_FILES):
        missing = sorted(set(PACKAGE_FILES) - observed)
        extra = sorted(observed - set(PACKAGE_FILES))
        _reject(f"source package inventory mismatch; missing={missing}, extra={extra}")

    package = {
        name: _read_regular_file(
            source / name, f"source package file {name}", MAX_MEMBER_BYTES
        )
        for name in PACKAGE_FILES
    }
    license_payload = _read_regular_file(
        source / "LICENSE", "source license", MAX_MEMBER_BYTES
    )
    if hashlib.sha256(license_payload).hexdigest() != LICENSE_SHA256:
        _reject("source LICENSE does not match the AGPL-3.0-or-later release license")
    readme = _read_regular_file(source / "README.md", "source README", MAX_MEMBER_BYTES)
    if not readme.endswith(b"\n") or readme.endswith(b"\n\n"):
        _reject("source README must end with exactly one newline")
    pyproject = _read_regular_file(
        source / "pyproject.toml", "source pyproject", MAX_MEMBER_BYTES
    )
    _validate_source_pyproject(pyproject)
    return SourcePayloads(package, license_payload, readme, pyproject)


def _wheel_timestamp(epoch: int) -> tuple[int, int, int, int, int, int]:
    try:
        timestamp = dt.datetime.fromtimestamp(epoch, tz=dt.UTC)
    except (OverflowError, OSError, ValueError) as exc:
        _reject(f"epoch is not representable: {exc}")
    if timestamp.year < 1980 or timestamp.year > 2107:
        _reject("epoch is outside the ZIP timestamp range")
    return (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second - timestamp.second % 2,
    )


def _zip_datetime_fields(
    timestamp: tuple[int, int, int, int, int, int],
) -> tuple[int, int]:
    year, month, day, hour, minute, second = timestamp
    dos_time = (hour << 11) | (minute << 5) | (second // 2)
    dos_date = ((year - 1980) << 9) | (month << 5) | day
    return dos_time, dos_date


def _decompress_zip_member(
    compressed: bytes, info: zipfile.ZipInfo, remaining: int
) -> bytes:
    limit = min(MAX_MEMBER_BYTES, remaining, info.file_size)
    if info.file_size > MAX_MEMBER_BYTES:
        _reject(f"wheel member exceeds its size bound: {info.filename}")
    if info.file_size > remaining:
        _reject("wheel expanded size exceeds its bound")

    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    output = bytearray()
    chunk = compressed
    while chunk:
        allowance = limit + 1 - len(output)
        if allowance <= 0:
            _reject(f"wheel member expands beyond its declared size: {info.filename}")
        try:
            output.extend(decompressor.decompress(chunk, allowance))
        except zlib.error as exc:
            _reject(
                f"wheel member has an invalid DEFLATE stream: {info.filename}: {exc}"
            )
        if len(output) > limit:
            _reject(f"wheel member expands beyond its declared size: {info.filename}")
        chunk = decompressor.unconsumed_tail
        if decompressor.eof:
            if decompressor.unused_data or chunk:
                _reject(f"wheel member has trailing compressed data: {info.filename}")
            break
    if not decompressor.eof:
        _reject(f"wheel member DEFLATE stream is truncated: {info.filename}")

    payload = bytes(output)
    if len(payload) != info.file_size:
        _reject(f"wheel member size mismatch: {info.filename}")
    if zlib.crc32(payload) & 0xFFFFFFFF != info.CRC:
        _reject(f"wheel member CRC mismatch: {info.filename}")
    return payload


def _load_canonical_zip_members(
    artifact: bytes,
    infos: list[zipfile.ZipInfo],
    expected_timestamp: tuple[int, int, int, int, int, int],
) -> dict[str, bytes]:
    if len(artifact) < ZIP_END_RECORD.size:
        _reject("wheel ZIP end record is truncated")
    end_offset = len(artifact) - ZIP_END_RECORD.size
    end_record = ZIP_END_RECORD.unpack_from(artifact, end_offset)
    (
        signature,
        disk_number,
        directory_disk,
        entries_on_disk,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = end_record
    if signature != b"PK\x05\x06" or comment_size:
        _reject("wheel ZIP envelope is not canonical")
    if (disk_number, directory_disk) != (0, 0):
        _reject("wheel ZIP must not span disks")
    if entries_on_disk != len(infos) or total_entries != len(infos):
        _reject("wheel ZIP entry counts do not match its inventory")
    if directory_offset + directory_size != end_offset:
        _reject("wheel ZIP central-directory bounds are not canonical")

    dos_time, dos_date = _zip_datetime_fields(expected_timestamp)
    contents: dict[str, bytes] = {}
    local_offset = 0
    consumed = 0
    for info in infos:
        if local_offset != info.header_offset:
            _reject(f"wheel local-header offset mismatch: {info.filename}")
        if local_offset + ZIP_LOCAL_HEADER.size > directory_offset:
            _reject(f"wheel local header is truncated: {info.filename}")
        local = ZIP_LOCAL_HEADER.unpack_from(artifact, local_offset)
        try:
            encoded_name = info.filename.encode("ascii")
        except UnicodeEncodeError:
            _reject(f"wheel member name is not canonical ASCII: {info.filename}")
        expected_local = (
            b"PK\x03\x04",
            20,
            0,
            zipfile.ZIP_DEFLATED,
            dos_time,
            dos_date,
            info.CRC,
            info.compress_size,
            info.file_size,
            len(encoded_name),
            0,
        )
        if local != expected_local:
            _reject(f"wheel local header is not canonical: {info.filename}")
        name_start = local_offset + ZIP_LOCAL_HEADER.size
        name_end = name_start + len(encoded_name)
        if artifact[name_start:name_end] != encoded_name:
            _reject(f"wheel local filename mismatch: {info.filename}")
        data_end = name_end + info.compress_size
        if data_end > directory_offset:
            _reject(f"wheel compressed member is out of bounds: {info.filename}")
        payload = _decompress_zip_member(
            artifact[name_end:data_end], info, MAX_EXPANDED_BYTES - consumed
        )
        consumed += len(payload)
        contents[info.filename] = payload
        local_offset = data_end
    if local_offset != directory_offset:
        _reject("wheel has data outside its canonical local members")

    central_offset = directory_offset
    expected_external_modes: dict[str, int] = {
        name: (0o644 if name == RECORD_PATH else 0o100644) << 16
        for name in WHEEL_MEMBERS
    }
    for info in infos:
        if central_offset + ZIP_CENTRAL_HEADER.size > end_offset:
            _reject(f"wheel central header is truncated: {info.filename}")
        central = ZIP_CENTRAL_HEADER.unpack_from(artifact, central_offset)
        encoded_name = info.filename.encode("ascii")
        expected_central = (
            b"PK\x01\x02",
            (3 << 8) | 20,
            20,
            0,
            zipfile.ZIP_DEFLATED,
            dos_time,
            dos_date,
            info.CRC,
            info.compress_size,
            info.file_size,
            len(encoded_name),
            0,
            0,
            0,
            0,
            expected_external_modes[info.filename],
            info.header_offset,
        )
        if central != expected_central:
            _reject(f"wheel central header is not canonical: {info.filename}")
        name_start = central_offset + ZIP_CENTRAL_HEADER.size
        name_end = name_start + len(encoded_name)
        if artifact[name_start:name_end] != encoded_name:
            _reject(f"wheel central filename mismatch: {info.filename}")
        central_offset = name_end
    if central_offset != end_offset:
        _reject("wheel central-directory size is not canonical")
    return contents


def _load_wheel(path: Path, epoch: int) -> tuple[bytes, dict[str, bytes]]:
    artifact = _read_regular_file(path, "wheel", MAX_ARCHIVE_BYTES)
    try:
        archive = zipfile.ZipFile(io.BytesIO(artifact), "r")
    except zipfile.BadZipFile as exc:
        _reject(f"wheel is not a valid ZIP archive: {exc}")

    with archive:
        if archive.comment:
            _reject("wheel has an unexpected archive comment")
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            _reject("wheel has too many members")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            _reject("wheel contains duplicate members")
        unsafe = [name for name in names if not _safe_member_name(name)]
        if unsafe:
            _reject(f"wheel contains unsafe members: {unsafe}")
        if tuple(names) != WHEEL_MEMBERS:
            missing = sorted(set(WHEEL_MEMBERS) - set(names))
            extra = sorted(set(names) - set(WHEEL_MEMBERS))
            _reject(f"wheel inventory mismatch; missing={missing}, extra={extra}")

        expanded_size = sum(info.file_size for info in infos)
        if expanded_size > MAX_EXPANDED_BYTES:
            _reject("wheel expanded size exceeds its bound")
        expected_timestamp = _wheel_timestamp(epoch)
        for info in infos:
            if info.is_dir():
                _reject(f"wheel contains a directory member: {info.filename}")
            if info.create_system != 3:
                _reject(f"wheel member has a non-Unix origin: {info.filename}")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            expected_mode = 0o644 if info.filename == RECORD_PATH else 0o100644
            if unix_mode != expected_mode:
                _reject(
                    f"wheel member mode mismatch for {info.filename}: {oct(unix_mode)}"
                )
            file_type = stat.S_IFMT(unix_mode)
            if file_type not in {0, stat.S_IFREG}:
                _reject(f"wheel contains a special member: {info.filename}")
            if info.date_time != expected_timestamp:
                _reject(f"wheel member epoch mismatch: {info.filename}")
            if info.compress_type != zipfile.ZIP_DEFLATED:
                _reject(f"wheel member compression mismatch: {info.filename}")
            if info.flag_bits != 0 or info.extra or info.comment:
                _reject(f"wheel member has unexpected ZIP metadata: {info.filename}")
        contents = _load_canonical_zip_members(artifact, infos, expected_timestamp)
    return artifact, contents


def _validate_gzip_header(artifact: bytes, epoch: int) -> None:
    if len(artifact) < 12:
        _reject("sdist gzip header is truncated")
    if artifact[:4] != b"\x1f\x8b\x08\x08":
        _reject("sdist gzip method or flags are not exact")
    if struct.unpack("<I", artifact[4:8])[0] != epoch:
        _reject("sdist gzip epoch does not match the release epoch")
    if artifact[8:10] != b"\x02\xff":
        _reject("sdist gzip compression or OS marker is not exact")
    filename_end = artifact.find(b"\x00", 10, 256)
    if filename_end < 0:
        _reject("sdist gzip filename is missing or unbounded")
    expected_filename = f"{SDIST_ROOT}.tar".encode("ascii")
    if artifact[10:filename_end] != expected_filename:
        _reject("sdist gzip filename is not exact")


def _decompress_gzip_bounded(artifact: bytes) -> bytes:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    offset = 0
    while offset < len(artifact):
        chunk = artifact[offset : offset + 64 * 1024]
        offset += len(chunk)
        while chunk:
            remaining = MAX_EXPANDED_BYTES + 1 - len(output)
            if remaining <= 0:
                _reject("sdist expanded size exceeds its bound")
            try:
                output.extend(decompressor.decompress(chunk, remaining))
            except zlib.error as exc:
                _reject(f"sdist gzip stream is invalid: {exc}")
            if len(output) > MAX_EXPANDED_BYTES:
                _reject("sdist expanded size exceeds its bound")
            chunk = decompressor.unconsumed_tail
            if decompressor.eof:
                if decompressor.unused_data or chunk or offset != len(artifact):
                    _reject("sdist gzip stream has trailing or concatenated data")
                break
        if decompressor.eof:
            break
    if not decompressor.eof:
        _reject("sdist gzip stream is truncated")
    try:
        output.extend(decompressor.flush())
    except zlib.error as exc:
        _reject(f"sdist gzip trailer is invalid: {exc}")
    if len(output) > MAX_EXPANDED_BYTES:
        _reject("sdist expanded size exceeds its bound")
    return bytes(output)


def _read_tar_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, remaining: int
) -> bytes:
    if member.size > MAX_MEMBER_BYTES:
        _reject(f"sdist member exceeds its size bound: {member.name}")
    if member.size > remaining:
        _reject("sdist member sizes exceed the expansion bound")
    stream = archive.extractfile(member)
    if stream is None:
        _reject(f"sdist member cannot be read: {member.name}")
    with stream:
        payload = stream.read(member.size + 1)
    if len(payload) != member.size:
        _reject(f"sdist member size mismatch: {member.name}")
    return payload


def _canonical_tar_header(name: str, size: int, epoch: int) -> bytes:
    encoded_name = name.encode("ascii")
    if len(encoded_name) > 100:
        _reject(f"canonical sdist member name is too long: {name}")
    header = bytearray(tarfile.BLOCKSIZE)
    header[: len(encoded_name)] = encoded_name
    header[100:108] = f"{0o644:07o}\0".encode("ascii")
    header[108:116] = b"0000000\0"
    header[116:124] = b"0000000\0"
    header[124:136] = f"{size:011o}\0".encode("ascii")
    header[136:148] = f"{epoch:011o}\0".encode("ascii")
    header[148:156] = b"        "
    header[156:157] = tarfile.REGTYPE
    header[257:263] = b"ustar\0"
    header[263:265] = b"00"
    header[148:156] = f"{sum(header):06o}\0 ".encode("ascii")
    return bytes(header)


def _validate_tar_layout(
    raw_tar: bytes, contents: dict[str, bytes], epoch: int
) -> None:
    offset = 0
    for name in SDIST_MEMBERS:
        payload = contents[name]
        header_end = offset + tarfile.BLOCKSIZE
        if header_end > len(raw_tar):
            _reject(f"sdist physical header is truncated: {name}")
        header = raw_tar[offset:header_end]
        if header[156:157] != tarfile.REGTYPE:
            _reject("sdist contains a hidden or special physical record")
        if header != _canonical_tar_header(name, len(payload), epoch):
            _reject(f"sdist physical header is not canonical: {name}")
        data_end = header_end + len(payload)
        padded_end = (
            (data_end + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
        ) * tarfile.BLOCKSIZE
        if padded_end > len(raw_tar):
            _reject(f"sdist physical member is truncated: {name}")
        if raw_tar[header_end:data_end] != payload:
            _reject(f"sdist physical payload differs from parsed member: {name}")
        if any(raw_tar[data_end:padded_end]):
            _reject(f"sdist member padding is not zero-filled: {name}")
        offset = padded_end

    minimum_end = offset + 2 * tarfile.BLOCKSIZE
    expected_size = (
        (minimum_end + tarfile.RECORDSIZE - 1) // tarfile.RECORDSIZE
    ) * tarfile.RECORDSIZE
    if len(raw_tar) != expected_size or any(raw_tar[offset:]):
        _reject("sdist tar stream has a non-canonical trailer")


def _load_sdist(path: Path, epoch: int) -> tuple[bytes, dict[str, bytes]]:
    artifact = _read_regular_file(path, "sdist", MAX_ARCHIVE_BYTES)
    _validate_gzip_header(artifact, epoch)
    raw_tar = _decompress_gzip_bounded(artifact)
    if len(raw_tar) % tarfile.RECORDSIZE:
        _reject("sdist tar stream is not record-aligned")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:")
    except tarfile.TarError as exc:
        _reject(f"sdist tar stream is invalid: {exc}")

    with archive:
        if archive.pax_headers:
            _reject("sdist contains global PAX metadata")
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            _reject("sdist has too many members")
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            _reject("sdist contains duplicate members")
        unsafe = [name for name in names if not _safe_member_name(name)]
        if unsafe:
            _reject(f"sdist contains unsafe members: {unsafe}")
        special = [member.name for member in members if member.type != tarfile.REGTYPE]
        if special:
            _reject(f"sdist contains special members: {special}")
        if tuple(names) != SDIST_MEMBERS:
            missing = sorted(set(SDIST_MEMBERS) - set(names))
            extra = sorted(set(names) - set(SDIST_MEMBERS))
            _reject(f"sdist inventory mismatch; missing={missing}, extra={extra}")

        declared_size = sum(member.size for member in members)
        if declared_size > MAX_EXPANDED_BYTES:
            _reject("sdist member sizes exceed the expansion bound")
        contents: dict[str, bytes] = {}
        consumed = 0
        for member in members:
            if member.mode != 0o644:
                _reject(f"sdist member mode mismatch: {member.name}")
            if member.mtime != epoch:
                _reject(f"sdist member epoch mismatch: {member.name}")
            if (member.uid, member.gid, member.uname, member.gname) != (0, 0, "", ""):
                _reject(f"sdist member ownership mismatch: {member.name}")
            if member.linkname or member.pax_headers or getattr(member, "sparse", None):
                _reject(f"sdist member has unexpected metadata: {member.name}")
            payload = _read_tar_member(archive, member, MAX_EXPANDED_BYTES - consumed)
            consumed += len(payload)
            contents[member.name] = payload

        _validate_tar_layout(raw_tar, contents, epoch)
    return artifact, contents


def _validate_metadata(metadata: bytes, readme: bytes) -> None:
    if b"\r" in metadata:
        _reject("package metadata must use canonical LF line endings")
    header, separator, description = metadata.partition(b"\n\n")
    if not separator:
        _reject("package metadata has no description separator")
    try:
        message = BytesParser(policy=default).parsebytes(metadata)
    except (UnicodeError, ValueError) as exc:
        _reject(f"package metadata cannot be parsed: {exc}")
    if message.defects:
        _reject(f"package metadata has parser defects: {message.defects}")
    if tuple(message.raw_items()) != EXPECTED_METADATA_HEADERS:
        _reject("package metadata headers do not match the exact release contract")
    if header.count(b"\n") + 1 != len(EXPECTED_METADATA_HEADERS):
        _reject("package metadata contains folded or unexpected header lines")
    if description != readme + b"\n":
        _reject("package metadata description does not match README.md")


def _record_hash(payload: bytes) -> str:
    digest = hashlib.sha256(payload).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _validate_record(contents: dict[str, bytes]) -> None:
    record = contents[RECORD_PATH]
    if not record.endswith(b"\n") or b"\r" in record:
        _reject("wheel RECORD line endings are not canonical")
    try:
        text = record.decode("utf-8")
    except UnicodeDecodeError as exc:
        _reject(f"wheel RECORD is not UTF-8: {exc}")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if len(rows) != len(WHEEL_MEMBERS):
        _reject("wheel RECORD row count does not match the wheel inventory")
    for expected_name, row in zip(WHEEL_MEMBERS, rows, strict=True):
        if len(row) != 3 or row[0] != expected_name:
            _reject("wheel RECORD paths or columns do not match the wheel inventory")
        if expected_name == RECORD_PATH:
            if row[1:] != ["", ""]:
                _reject("wheel RECORD must not hash itself")
            continue
        expected_hash = _record_hash(contents[expected_name])
        expected_size = str(len(contents[expected_name]))
        if row[1:] != [expected_hash, expected_size]:
            _reject(f"wheel RECORD digest or size mismatch: {expected_name}")
    expected_record = "".join(
        (
            f"{name},{_record_hash(contents[name])},{len(contents[name])}\n"
            if name != RECORD_PATH
            else f"{RECORD_PATH},,\n"
        )
        for name in WHEEL_MEMBERS
    ).encode("utf-8")
    if record != expected_record:
        _reject("wheel RECORD serialization is not canonical")


def verify_artifacts(dist: Path, source: Path, epoch: int) -> VerificationReceipt:
    """Verify exact wheel/sdist provenance and return immutable hashes."""

    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or not 0 <= epoch <= 0xFFFFFFFF
    ):
        _reject("epoch must be an unsigned 32-bit integer")
    _wheel_timestamp(epoch)
    _require_directory(dist, "distribution directory")
    source_payloads = _load_source(source)

    entries = sorted(path.name for path in dist.iterdir())
    expected_entries = sorted((WHEEL_FILENAME, SDIST_FILENAME))
    if entries != expected_entries:
        _reject(
            f"distribution inventory mismatch; expected={expected_entries}, got={entries}"
        )

    wheel_artifact, wheel = _load_wheel(dist / WHEEL_FILENAME, epoch)
    sdist_artifact, sdist = _load_sdist(dist / SDIST_FILENAME, epoch)

    for name, source_payload in source_payloads.package.items():
        if wheel[name] != source_payload:
            _reject(f"wheel package content differs from source: {name}")
        sdist_name = f"{SDIST_ROOT}/{name}"
        if sdist[sdist_name] != source_payload:
            _reject(f"sdist package content differs from source: {name}")

    if wheel[WHEEL_LICENSE_PATH] != source_payloads.license:
        _reject("wheel license differs from source LICENSE")
    if sdist[f"{SDIST_ROOT}/LICENSE"] != source_payloads.license:
        _reject("sdist license differs from source LICENSE")
    if sdist[f"{SDIST_ROOT}/README.md"] != source_payloads.readme:
        _reject("sdist README differs from source README.md")
    if sdist[f"{SDIST_ROOT}/pyproject.toml"] != source_payloads.pyproject:
        _reject("sdist pyproject differs from source pyproject.toml")

    metadata = wheel[METADATA_PATH]
    if metadata != sdist[f"{SDIST_ROOT}/PKG-INFO"]:
        _reject("wheel METADATA and sdist PKG-INFO are not byte-identical")
    _validate_metadata(metadata, source_payloads.readme)
    if wheel[WHEEL_PATH] != EXPECTED_WHEEL:
        _reject("wheel WHEEL metadata does not match Poetry Core 2.4.1")
    if wheel[ENTRY_POINTS_PATH] != EXPECTED_ENTRY_POINTS:
        _reject("wheel entry points do not match the OpenBB plugin contract")
    _validate_record(wheel)

    return VerificationReceipt(
        wheel_sha256=hashlib.sha256(wheel_artifact).hexdigest(),
        wheel_size=len(wheel_artifact),
        sdist_sha256=hashlib.sha256(sdist_artifact).hexdigest(),
        sdist_size=len(sdist_artifact),
    )


def _epoch(value: str) -> int:
    try:
        epoch = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("epoch must be a base-10 integer") from exc
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError("epoch must be an unsigned 32-bit integer")
    return epoch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--epoch", required=True, type=_epoch)
    arguments = parser.parse_args(argv)
    try:
        receipt = verify_artifacts(arguments.dist, arguments.source, arguments.epoch)
    except ArtifactVerificationError as exc:
        print(f"artifact verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"verified {WHEEL_FILENAME} sha256:{receipt.wheel_sha256} "
        f"bytes:{receipt.wheel_size}"
    )
    print(
        f"verified {SDIST_FILENAME} sha256:{receipt.sdist_sha256} "
        f"bytes:{receipt.sdist_size}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
