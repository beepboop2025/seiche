#!/usr/bin/env python3
"""Fail-closed verification for Seiche's wheel and source distribution."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import gzip
import hashlib
import io
import os
import re
import stat
import struct
import tarfile
import tomllib
import zipfile
import zlib
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath


MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_ENTRY_POINTS_BYTES = 64 * 1024
MAX_WHEEL_METADATA_BYTES = 16 * 1024
MINIMUM_ZIP_EPOCH = 315532800  # 1980-01-01T00:00:00Z


class VerificationError(RuntimeError):
    """The built package is not exactly the reviewed Seiche distribution."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _safe_member_name(name: str, *, artifact: str) -> None:
    _require(bool(name), f"{artifact} contains an empty member name")
    _require(
        "\\" not in name and "\x00" not in name,
        f"unsafe member in {artifact}: {name!r}",
    )
    path = PurePosixPath(name)
    _require(not path.is_absolute(), f"absolute member in {artifact}: {name!r}")
    parts = name.split("/")
    _require(
        all(part not in {"", ".", ".."} for part in parts),
        f"non-canonical member in {artifact}: {name!r}",
    )


def _metadata_body(raw: bytes, *, artifact: str) -> bytes:
    sections = re.split(rb"\r?\n\r?\n", raw, maxsplit=1)
    _require(len(sections) == 2, f"{artifact} metadata has no description body")
    return sections[1]


def _optional_requirement(requirement: str, extra: str) -> str:
    if ";" not in requirement:
        return f"{requirement}; extra == '{extra}'"
    dependency, marker = requirement.split(";", 1)
    return f"{dependency.strip()}; ({marker.strip()}) and extra == '{extra}'"


def _contact_header(contacts: list[dict]) -> str:
    rendered = []
    for contact in contacts:
        name = contact.get("name", "")
        email = contact.get("email", "")
        rendered.append(f"{name} <{email}>" if email else name)
    return ", ".join(rendered)


def _bounded_gzip_decompress(body: bytes, limit: int) -> bytes:
    _require(0 < limit <= MAX_EXPANDED_BYTES, "invalid gzip expansion budget")
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    expanded = decompressor.decompress(body, limit + 1)
    _require(len(expanded) <= limit, "sdist expands beyond its reviewed size budget")
    _require(
        decompressor.eof, "sdist gzip stream is truncated or exceeds its size budget"
    )
    _require(
        not decompressor.unconsumed_tail, "sdist gzip input was not fully consumed"
    )
    _require(
        not decompressor.unused_data,
        "sdist contains trailing or concatenated gzip data",
    )
    epoch = struct.unpack("<I", body[4:8])[0]
    _require(
        body == gzip.compress(expanded, compresslevel=9, mtime=epoch),
        "sdist gzip stream is not byte-canonical",
    )
    return expanded


def _read_bounded_regular_file(path: Path, *, artifact: str) -> bytes:
    """Read an artifact only after pinning and sizing its regular-file inode."""
    _require(not path.is_symlink(), f"{artifact} must not be a symlink")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        _require(
            stat.S_ISREG(metadata.st_mode),
            f"{artifact} must be a regular file",
        )
        _require(
            0 < metadata.st_size <= MAX_ARTIFACT_BYTES,
            f"unsafe {artifact} size: {metadata.st_size}",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            body = stream.read(MAX_ARTIFACT_BYTES + 1)
        _require(
            len(body) == metadata.st_size,
            f"{artifact} size changed while it was being verified",
        )
        return body
    finally:
        os.close(descriptor)


def _verify_gzip_header(body: bytes, epoch: int, *, artifact: str) -> None:
    _require(len(body) >= 10, f"{artifact} has no complete gzip header")
    _require(
        body[:4] == b"\x1f\x8b\x08\x00",
        f"{artifact} must use deterministic gzip headers",
    )
    _require(
        struct.unpack("<I", body[4:8])[0] == epoch,
        f"{artifact} gzip epoch differs from Git",
    )
    _require(
        body[8:10] == b"\x02\xff",
        f"{artifact} gzip XFL or OS byte drift",
    )


def _verify_zip_framing(
    body: bytes,
    infos: list[zipfile.ZipInfo],
    *,
    artifact: str,
    package_members: set[str],
) -> None:
    _require(len(body) >= 22, f"{artifact} has no complete ZIP end record")
    end = struct.unpack_from("<4s4H2IH", body, len(body) - 22)
    (
        signature,
        disk,
        directory_disk,
        disk_entries,
        total_entries,
        directory_size,
        directory_offset,
        comment_size,
    ) = end
    _require(
        signature == b"PK\x05\x06",
        f"{artifact} has trailing or missing ZIP end metadata",
    )
    _require(disk == directory_disk == 0, f"{artifact} uses a multi-disk ZIP layout")
    _require(
        disk_entries == total_entries == len(infos),
        f"{artifact} ZIP member count drift",
    )
    _require(
        comment_size == 0, f"{artifact} contains a ZIP comment or trailing envelope"
    )
    _require(
        directory_offset + directory_size + 22 == len(body),
        f"{artifact} central directory does not exactly frame the archive",
    )

    central_offset = directory_offset
    expected_local_offset = 0
    for info in infos:
        _require(
            central_offset + 46 <= directory_offset + directory_size,
            f"{artifact} has a truncated central-directory record",
        )
        central = struct.unpack_from("<4s6H3I5H2I", body, central_offset)
        (
            central_signature,
            made_by,
            needed,
            flags,
            compression,
            modified_time,
            modified_date,
            crc,
            compressed_size,
            expanded_size,
            name_size,
            extra_size,
            member_comment_size,
            start_disk,
            internal_attributes,
            external_attributes,
            local_offset,
        ) = central
        _require(
            central_signature == b"PK\x01\x02", f"{artifact} central-directory drift"
        )
        record_end = central_offset + 46 + name_size + extra_size + member_comment_size
        _require(
            record_end <= directory_offset + directory_size,
            f"{artifact} has a truncated central-directory payload",
        )
        central_name = body[central_offset + 46 : central_offset + 46 + name_size]
        central_extra = body[
            central_offset + 46 + name_size : central_offset
            + 46
            + name_size
            + extra_size
        ]
        central_comment = body[
            central_offset + 46 + name_size + extra_size : record_end
        ]
        try:
            expected_name = info.filename.encode("ascii")
        except UnicodeEncodeError as exc:
            raise VerificationError(
                f"{artifact} contains a non-ASCII wheel member"
            ) from exc
        _require(central_name == expected_name, f"{artifact} central member-name drift")
        _require(
            not central_extra and not central_comment,
            f"{artifact} has hidden ZIP metadata",
        )
        _require(
            flags == 0, f"{artifact} uses unreviewed ZIP flags for {info.filename}"
        )
        _require(
            made_by == (3 << 8) | 20 and needed == 20,
            f"{artifact} ZIP version drift",
        )
        _require(start_disk == 0, f"{artifact} member starts on another ZIP disk")
        expected_external_attributes = (
            0o100644 if info.filename in package_members else 0o644
        ) << 16
        _require(
            internal_attributes == 0
            and external_attributes == expected_external_attributes,
            f"{artifact} ZIP attributes drift: {info.filename}",
        )
        _require(
            (
                compression,
                crc,
                compressed_size,
                expanded_size,
                internal_attributes,
                external_attributes,
                local_offset,
            )
            == (
                info.compress_type,
                info.CRC,
                info.compress_size,
                info.file_size,
                info.internal_attr,
                info.external_attr,
                info.header_offset,
            ),
            f"{artifact} central-directory values differ from its parsed member",
        )
        _require(
            local_offset == expected_local_offset,
            f"{artifact} hides bytes between ZIP members",
        )
        _require(
            local_offset + 30 <= directory_offset,
            f"{artifact} has a truncated local header",
        )
        local = struct.unpack_from("<4s5H3I2H", body, local_offset)
        (
            local_signature,
            local_needed,
            local_flags,
            local_compression,
            local_time,
            local_date,
            local_crc,
            local_compressed_size,
            local_expanded_size,
            local_name_size,
            local_extra_size,
        ) = local
        _require(local_signature == b"PK\x03\x04", f"{artifact} local-header drift")
        _require(
            (
                local_needed,
                local_flags,
                local_compression,
                local_time,
                local_date,
                local_crc,
                local_compressed_size,
                local_expanded_size,
            )
            == (
                needed,
                flags,
                compression,
                modified_time,
                modified_date,
                crc,
                compressed_size,
                expanded_size,
            ),
            f"{artifact} local and central ZIP headers disagree for {info.filename}",
        )
        local_name_start = local_offset + 30
        local_data_start = local_name_start + local_name_size + local_extra_size
        _require(
            local_data_start <= directory_offset,
            f"{artifact} local metadata is truncated",
        )
        local_name = body[local_name_start : local_name_start + local_name_size]
        local_extra = body[local_name_start + local_name_size : local_data_start]
        _require(local_name == central_name, f"{artifact} local member-name drift")
        _require(not local_extra, f"{artifact} has hidden local ZIP metadata")
        local_data_end = local_data_start + compressed_size
        _require(
            local_data_end <= directory_offset, f"{artifact} member data is truncated"
        )
        compressed = body[local_data_start:local_data_end]
        _require(compression == zipfile.ZIP_DEFLATED, f"{artifact} compression drift")
        decoder = zlib.decompressobj(-zlib.MAX_WBITS)
        expanded = decoder.decompress(compressed, expanded_size + 1)
        _require(
            len(expanded) == expanded_size,
            f"{artifact} deflate output differs from its declared size: {info.filename}",
        )
        _require(
            decoder.eof,
            f"{artifact} has a truncated deflate stream: {info.filename}",
        )
        _require(
            not decoder.unconsumed_tail and not decoder.unused_data,
            f"{artifact} hides data inside a deflate stream: {info.filename}",
        )
        _require(
            compressed
            == zlib.compress(
                expanded,
                level=-1,
                wbits=-zlib.MAX_WBITS,
            ),
            f"{artifact} has a non-canonical deflate stream: {info.filename}",
        )
        expected_local_offset = local_data_end
        central_offset = record_end

    _require(
        central_offset == directory_offset + directory_size,
        f"{artifact} central-directory size drift",
    )
    _require(
        expected_local_offset == directory_offset,
        f"{artifact} hides bytes before its central directory",
    )


def _tar_octal(field: bytes, *, artifact: str, label: str) -> int:
    _require(field.endswith(b"\0"), f"{artifact} has non-canonical TAR {label}")
    digits = field[:-1]
    _require(
        bool(re.fullmatch(rb"[0-7]+", digits)), f"{artifact} has invalid TAR {label}"
    )
    return int(digits, 8)


def _verify_tar_framing(
    body: bytes,
    *,
    artifact: str,
    expected_sizes: dict[str, int | None],
    epoch: int,
) -> None:
    _require(len(body) % 10240 == 0, f"{artifact} TAR record size drift")
    zero_block = bytes(512)
    offset = 0
    seen: set[str] = set()
    while offset + 512 <= len(body):
        header = body[offset : offset + 512]
        if header == zero_block:
            _require(
                offset + 1024 <= len(body)
                and body[offset + 512 : offset + 1024] == zero_block,
                f"{artifact} has an incomplete TAR end marker",
            )
            _require(
                not any(body[offset:]),
                f"{artifact} hides data after its TAR end marker",
            )
            break
        _require(
            len(seen) < len(expected_sizes), f"{artifact} has extra raw TAR records"
        )
        name_field = header[:100]
        name_bytes, separator, name_padding = name_field.partition(b"\0")
        _require(
            separator == b"\0" and not any(name_padding),
            f"{artifact} TAR name is non-canonical",
        )
        try:
            name = name_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise VerificationError(f"{artifact} has a non-ASCII TAR member") from exc
        _safe_member_name(name, artifact=artifact)
        _require(
            name in expected_sizes, f"{artifact} contains a hidden TAR record: {name}"
        )
        _require(
            name not in seen, f"{artifact} contains a duplicate raw TAR member: {name}"
        )
        seen.add(name)
        mode = _tar_octal(header[100:108], artifact=artifact, label="mode")
        uid = _tar_octal(header[108:116], artifact=artifact, label="uid")
        gid = _tar_octal(header[116:124], artifact=artifact, label="gid")
        size = _tar_octal(header[124:136], artifact=artifact, label="size")
        mtime = _tar_octal(header[136:148], artifact=artifact, label="mtime")
        _require(header[154:156] == b"\0 ", f"{artifact} TAR checksum encoding drift")
        checksum = _tar_octal(header[148:155], artifact=artifact, label="checksum")
        calculated = sum(header[:148]) + sum(b" " * 8) + sum(header[156:])
        _require(
            checksum == calculated, f"{artifact} TAR header checksum mismatch: {name}"
        )
        _require(
            header[156:157] == b"0",
            f"{artifact} has a TAR pseudo or special record: {name}",
        )
        _require(not any(header[157:257]), f"{artifact} TAR link target drift: {name}")
        _require(
            header[257:265] == b"ustar\x0000",
            f"{artifact} TAR format drift: {name}",
        )
        _require(
            not any(header[265:500]), f"{artifact} hides extended TAR metadata: {name}"
        )
        _require(
            not any(header[500:512]), f"{artifact} TAR header padding drift: {name}"
        )
        _require(
            mode == 0o644 and uid == gid == 0, f"{artifact} TAR policy drift: {name}"
        )
        _require(mtime == epoch, f"{artifact} TAR timestamp drift: {name}")
        expected_size = expected_sizes[name]
        if expected_size is None:
            _require(
                size <= MAX_METADATA_BYTES, f"{artifact} metadata record is oversized"
            )
        else:
            _require(
                size == expected_size, f"{artifact} TAR declared size drift: {name}"
            )
        data_start = offset + 512
        data_end = data_start + size
        padded_end = data_start + ((size + 511) // 512) * 512
        _require(padded_end <= len(body), f"{artifact} TAR data is truncated: {name}")
        _require(
            not any(body[data_end:padded_end]),
            f"{artifact} has nonzero TAR data padding: {name}",
        )
        offset = padded_end
    else:
        raise VerificationError(f"{artifact} has no TAR end marker")
    _require(
        seen == set(expected_sizes),
        f"{artifact} raw TAR inventory differs from its contract",
    )


def _verify_metadata(
    raw: bytes,
    *,
    artifact: str,
    project: dict,
    readme: bytes,
) -> None:
    metadata = BytesParser(policy=default).parsebytes(raw)
    expected_scalars = {
        "Metadata-Version": "2.4",
        "Name": project["name"],
        "Version": project["version"],
        "Summary": project["description"],
        "License-Expression": project["license"],
        "Requires-Python": project["requires-python"],
        "Description-Content-Type": "text/markdown",
        "Keywords": ",".join(project["keywords"]),
    }
    for field, expected in expected_scalars.items():
        actual = metadata.get_all(field, [])
        _require(
            actual == [expected],
            f"{artifact} {field} values are {actual!r}, expected exactly {[expected]!r}",
        )

    for field, expected in {
        "Author-email": _contact_header(project["authors"]),
        "Maintainer-email": _contact_header(project["maintainers"]),
    }.items():
        actual = metadata.get_all(field, [])
        _require(
            actual == [expected],
            f"{artifact} {field} values are {actual!r}, expected exactly {[expected]!r}",
        )

    _require(
        metadata.get_all("License-File", []) == ["LICENSE"],
        f"{artifact} must declare exactly License-File: LICENSE",
    )
    expected_urls = {f"{label}, {url}" for label, url in project["urls"].items()}
    actual_urls = metadata.get_all("Project-URL", [])
    _require(
        len(actual_urls) == len(expected_urls) and set(actual_urls) == expected_urls,
        f"{artifact} project URLs differ from pyproject.toml",
    )
    actual_classifiers = metadata.get_all("Classifier", [])
    expected_classifiers = set(project["classifiers"])
    _require(
        len(actual_classifiers) == len(expected_classifiers)
        and set(actual_classifiers) == expected_classifiers,
        f"{artifact} classifiers differ from pyproject.toml",
    )

    optional = project.get("optional-dependencies", {})
    actual_extras = metadata.get_all("Provides-Extra", [])
    _require(
        len(actual_extras) == len(optional) and set(actual_extras) == set(optional),
        f"{artifact} extras differ from pyproject.toml",
    )
    expected_requirements = set(project["dependencies"])
    for extra, requirements in optional.items():
        expected_requirements.update(
            _optional_requirement(requirement, extra) for requirement in requirements
        )
    actual_requirements = metadata.get_all("Requires-Dist", [])
    _require(
        len(actual_requirements) == len(expected_requirements)
        and set(actual_requirements) == expected_requirements,
        f"{artifact} dependency metadata differs from pyproject.toml",
    )
    _require(
        _metadata_body(raw, artifact=artifact) == readme,
        f"{artifact} long description differs from backend/README.md",
    )


def _require_regular_source(path: Path, root: Path, *, label: str) -> Path:
    _require(not path.is_symlink(), f"{label} must not be a symlink: {path}")
    resolved = path.resolve(strict=True)
    _require(resolved.is_file(), f"{label} is not a regular file: {path}")
    _require(resolved.is_relative_to(root), f"{label} escapes its source root: {path}")
    return resolved


def _source_contract(source: Path, repository_license: Path) -> dict:
    _require(not source.is_symlink(), f"source root must not be a symlink: {source}")
    _require(
        not repository_license.is_symlink(), "repository license must not be a symlink"
    )
    source = source.resolve(strict=True)
    repository_license = repository_license.resolve(strict=True)
    repository_root = repository_license.parent
    repository_ignore = repository_license.parent / ".gitignore"
    pyproject_path = source / "pyproject.toml"
    readme_path = source / "README.md"
    package_license_path = source / "LICENSE"
    package_root = source / "seiche"
    _require(
        not package_root.is_symlink(),
        f"package root must not be a symlink: {package_root}",
    )
    _require(
        package_root.resolve(strict=True).is_relative_to(source),
        "package root escapes source",
    )
    pyproject_path = _require_regular_source(pyproject_path, source, label="pyproject")
    readme_path = _require_regular_source(readme_path, source, label="README")
    package_license_path = _require_regular_source(
        package_license_path, source, label="package license"
    )
    repository_ignore = _require_regular_source(
        repository_ignore, repository_root, label="repository ignore file"
    )

    document = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = document["project"]
    _require(
        project.get("license-files") == ["LICENSE"],
        "PEP 639 license-files must be [LICENSE]",
    )
    license_body = package_license_path.read_bytes()
    _require(
        license_body == repository_license.read_bytes(),
        "backend/LICENSE differs from the repository AGPL text",
    )

    package_files: dict[str, Path] = {}
    for path in package_root.rglob("*"):
        _require(
            not path.is_symlink(),
            f"package inventory must not contain symlinks: {path}",
        )
        if not path.is_file():
            continue
        resolved = path.resolve(strict=True)
        _require(
            resolved.is_relative_to(source), f"package source escapes root: {path}"
        )
        relative_to_package = path.relative_to(package_root)
        if "dispatches" in relative_to_package.parts:
            continue
        if path.suffix != ".py" and path.name != "bootstrap_snapshot.json":
            continue
        relative = path.relative_to(source).as_posix()
        package_files[relative] = resolved
    _require(package_files, "Seiche package source inventory is empty")
    return {
        "source": source,
        "project": project,
        "pyproject_path": pyproject_path,
        "readme_path": readme_path,
        "license_path": package_license_path,
        "repository_ignore_path": repository_ignore,
        "license_body": license_body,
        "readme_body": readme_path.read_bytes(),
        "package_files": package_files,
    }


def _verify_record(
    archive: zipfile.ZipFile, record_name: str, members: set[str]
) -> None:
    record_info = archive.getinfo(record_name)
    _require(record_info.file_size <= MAX_RECORD_BYTES, "wheel RECORD is oversized")
    record_body = archive.read(record_name)
    _require(len(record_body) == record_info.file_size, "wheel RECORD size drift")
    rows = list(csv.reader(io.StringIO(record_body.decode("utf-8"))))
    _require(all(len(row) == 3 for row in rows), "wheel RECORD has a malformed row")
    for row in rows:
        _safe_member_name(row[0], artifact="wheel RECORD")
    by_name = {row[0]: row[1:] for row in rows}
    _require(len(by_name) == len(rows), "wheel RECORD contains duplicate paths")
    _require(set(by_name) == members, "wheel RECORD inventory differs from the archive")
    for name, (digest, size) in by_name.items():
        if name == record_name:
            _require(not digest and not size, "wheel RECORD must not hash itself")
            continue
        body = archive.read(name)
        encoded = (
            base64.urlsafe_b64encode(hashlib.sha256(body).digest())
            .rstrip(b"=")
            .decode()
        )
        _require(
            digest == f"sha256={encoded}", f"wheel RECORD hash mismatch for {name}"
        )
        _require(size == str(len(body)), f"wheel RECORD size mismatch for {name}")


def _verify_sdist_member_metadata(
    member: tarfile.TarInfo,
    *,
    artifact: str,
    expected_names: set[str],
    seen: set[str],
    epoch: int,
) -> None:
    _require(
        len(seen) < len(expected_names),
        "sdist contains more members than its reviewed inventory",
    )
    _safe_member_name(member.name, artifact=artifact)
    _require(member.name not in seen, "sdist contains duplicate members")
    _require(member.name in expected_names, f"unreviewed sdist member: {member.name}")
    _require(member.isfile(), f"sdist contains a non-regular member: {member.name}")
    _require(member.mtime == epoch, f"sdist timestamp drift: {member.name}")
    _require(member.uid == member.gid == 0, f"sdist ownership drift: {member.name}")
    _require(
        member.uname == member.gname == "", f"sdist owner-name drift: {member.name}"
    )
    _require(member.mode == 0o644, f"sdist mode drift: {member.name}")
    _require(not member.pax_headers, f"sdist PAX metadata drift: {member.name}")


def _verify_wheel(path: Path, contract: dict, epoch: int) -> tuple[str, bytes]:
    project = contract["project"]
    distribution = re.sub(r"[-_.]+", "_", project["name"])
    version = project["version"].replace("-", "_")
    expected_filename = f"{distribution}-{version}-py3-none-any.whl"
    _require(path.name == expected_filename, f"unexpected wheel filename: {path.name}")
    body = _read_bounded_regular_file(path, artifact="wheel")
    dist_info = f"{distribution}-{version}.dist-info"
    expected_metadata_members = {
        f"{dist_info}/METADATA",
        f"{dist_info}/WHEEL",
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/RECORD",
    }
    expected_members = set(contract["package_files"]) | expected_metadata_members

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        _require(len(names) == len(set(names)), "wheel contains duplicate members")
        for name in names:
            _safe_member_name(name, artifact=path.name)
        _require(
            set(names) == expected_members,
            "wheel contains missing or unreviewed members",
        )
        _require(archive.comment == b"", "wheel contains an unreviewed ZIP comment")

        exact_sizes = {
            name: source_path.stat().st_size
            for name, source_path in contract["package_files"].items()
        }
        exact_sizes[f"{dist_info}/licenses/LICENSE"] = len(contract["license_body"])
        maximum_sizes = {
            f"{dist_info}/METADATA": MAX_METADATA_BYTES,
            f"{dist_info}/WHEEL": MAX_WHEEL_METADATA_BYTES,
            f"{dist_info}/entry_points.txt": MAX_ENTRY_POINTS_BYTES,
            f"{dist_info}/RECORD": MAX_RECORD_BYTES,
        }
        expanded_size = 0
        for info in infos:
            _require(
                not (info.flag_bits & 0x1), f"encrypted wheel member: {info.filename}"
            )
            _require(
                info.compress_type == zipfile.ZIP_DEFLATED,
                f"unexpected ZIP compression for {info.filename}",
            )
            if info.filename in exact_sizes:
                _require(
                    info.file_size == exact_sizes[info.filename],
                    f"wheel declared size differs from checkout: {info.filename}",
                )
            else:
                _require(
                    info.file_size <= maximum_sizes[info.filename],
                    f"wheel metadata member is oversized: {info.filename}",
                )
            expanded_size += info.file_size
            _require(
                expanded_size <= MAX_EXPANDED_BYTES,
                "wheel expands beyond its reviewed size budget",
            )
        package_members = set(contract["package_files"])
        _verify_zip_framing(
            body,
            infos,
            artifact=path.name,
            package_members=package_members,
        )
        _require(archive.testzip() is None, "wheel CRC validation failed")

        zip_epoch = max(epoch, MINIMUM_ZIP_EPOCH)
        expected_time = list(
            datetime.fromtimestamp(zip_epoch, timezone.utc).timetuple()[:6]
        )
        expected_time[-1] -= expected_time[-1] % 2
        for info in infos:
            _require(
                info.date_time == tuple(expected_time),
                f"wheel timestamp drift: {info.filename}",
            )
            expected_external_attributes = (
                0o100644 if info.filename in package_members else 0o644
            ) << 16
            _require(
                info.create_system == 3
                and info.create_version == 20
                and info.extract_version == 20
                and info.internal_attr == 0
                and info.external_attr == expected_external_attributes,
                f"wheel ZIP attributes drift: {info.filename}",
            )

        for name, source_path in contract["package_files"].items():
            _require(
                archive.read(name) == source_path.read_bytes(),
                f"wheel source differs: {name}",
            )
        license_name = f"{dist_info}/licenses/LICENSE"
        _require(
            archive.read(license_name) == contract["license_body"],
            "wheel AGPL text differs from backend/LICENSE",
        )
        metadata_name = f"{dist_info}/METADATA"
        metadata_raw = archive.read(metadata_name)
        _verify_metadata(
            metadata_raw,
            artifact=path.name,
            project=project,
            readme=contract["readme_body"],
        )

        entry_points = configparser.ConfigParser(interpolation=None)
        entry_points.optionxform = str
        entry_points.read_string(
            archive.read(f"{dist_info}/entry_points.txt").decode("utf-8")
        )
        _require(
            entry_points.sections() == ["console_scripts"],
            "unexpected wheel entry-point group",
        )
        _require(
            dict(entry_points["console_scripts"]) == project["scripts"],
            "wheel console entry points differ from pyproject.toml",
        )

        wheel_metadata = BytesParser(policy=default).parsebytes(
            archive.read(f"{dist_info}/WHEEL")
        )
        for field, expected in {
            "Wheel-Version": "1.0",
            "Generator": "hatchling 1.32.0",
            "Root-Is-Purelib": "true",
            "Tag": "py3-none-any",
        }.items():
            _require(
                wheel_metadata.get(field) == expected,
                f"wheel {field} is {wheel_metadata.get(field)!r}, expected {expected!r}",
            )
        _verify_record(archive, f"{dist_info}/RECORD", set(names))
    return _sha256(body), metadata_raw


def _verify_sdist(path: Path, contract: dict, epoch: int) -> tuple[str, bytes]:
    project = contract["project"]
    normalized_name = re.sub(r"[-_.]+", "-", project["name"]).lower()
    prefix = f"{normalized_name}-{project['version']}"
    expected_filename = f"{prefix}.tar.gz"
    _require(path.name == expected_filename, f"unexpected sdist filename: {path.name}")
    body = _read_bounded_regular_file(path, artifact="sdist")
    _verify_gzip_header(body, epoch, artifact="sdist")

    expected_source_files = {
        f"{prefix}/{relative}": source_path
        for relative, source_path in contract["package_files"].items()
    }
    expected_source_files.update(
        {
            f"{prefix}/LICENSE": contract["license_path"],
            f"{prefix}/README.md": contract["readme_path"],
            f"{prefix}/pyproject.toml": contract["pyproject_path"],
            f"{prefix}/.gitignore": contract["repository_ignore_path"],
        }
    )
    metadata_name = f"{prefix}/PKG-INFO"
    expected_names = set(expected_source_files) | {metadata_name}
    expected_source_sizes = {
        name: source_path.stat().st_size
        for name, source_path in expected_source_files.items()
    }
    expansion_budget = min(
        MAX_EXPANDED_BYTES,
        sum(expected_source_sizes.values())
        + MAX_METADATA_BYTES
        + (len(expected_names) * 4096)
        + 10240,
    )
    raw_tar = _bounded_gzip_decompress(body, expansion_budget)
    framing_sizes: dict[str, int | None] = dict(expected_source_sizes)
    framing_sizes[metadata_name] = None
    _verify_tar_framing(
        raw_tar,
        artifact=path.name,
        expected_sizes=framing_sizes,
        epoch=epoch,
    )

    seen: set[str] = set()
    metadata_raw = b""
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        for member in archive:
            _verify_sdist_member_metadata(
                member,
                artifact=path.name,
                expected_names=expected_names,
                seen=seen,
                epoch=epoch,
            )
            seen.add(member.name)
            if member.name == metadata_name:
                _require(
                    member.size <= MAX_METADATA_BYTES, "sdist PKG-INFO is oversized"
                )
                expected_size = member.size
            else:
                expected_size = expected_source_sizes[member.name]
                _require(
                    member.size == expected_size,
                    f"sdist declared size differs from checkout: {member.name}",
                )
            extracted = archive.extractfile(member)
            _require(
                extracted is not None, f"sdist member cannot be read: {member.name}"
            )
            member_body = extracted.read(expected_size + 1)
            _require(
                len(member_body) == expected_size,
                f"sdist member size drift: {member.name}",
            )
            if member.name == metadata_name:
                metadata_raw = member_body
            else:
                _require(
                    member_body == expected_source_files[member.name].read_bytes(),
                    f"sdist source differs: {member.name}",
                )
    _require(seen == expected_names, "sdist contains missing or unreviewed members")
    _verify_metadata(
        metadata_raw,
        artifact=path.name,
        project=project,
        readme=contract["readme_body"],
    )
    return _sha256(body), metadata_raw


def verify_distribution(
    dist: Path, source: Path, repository_license: Path, epoch: int
) -> None:
    _require(
        0 <= epoch <= 0xFFFFFFFF, "SOURCE_DATE_EPOCH must fit the gzip timestamp field"
    )
    dist = dist.resolve(strict=True)
    artifacts = sorted(path for path in dist.iterdir() if path.is_file())
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    _require(
        len(artifacts) == 2 and len(wheels) == len(sdists) == 1,
        "expected one wheel and one sdist",
    )
    contract = _source_contract(source, repository_license)
    wheel_digest, wheel_metadata = _verify_wheel(wheels[0], contract, epoch)
    sdist_digest, sdist_metadata = _verify_sdist(sdists[0], contract, epoch)
    _require(
        wheel_metadata == sdist_metadata,
        "wheel and sdist metadata differ byte-for-byte",
    )
    print(f"verified {wheels[0].name} sha256:{wheel_digest}")
    print(f"verified {sdists[0].name} sha256:{sdist_digest}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repository-license", type=Path, required=True)
    parser.add_argument("--epoch", type=int, required=True)
    args = parser.parse_args()
    try:
        verify_distribution(args.dist, args.source, args.repository_license, args.epoch)
    except (
        OSError,
        ValueError,
        VerificationError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
