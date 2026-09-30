"""Private inspection I/O preserves bytes, metadata and archive rejection."""

from __future__ import annotations

import errno
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import stat
import tarfile

import pytest

from seiche import recovery_inspection_io as bounded
from seiche import stateful_migration as migration


def scratch(tmp_path: Path, suffix: str = "test") -> Path:
    root = tmp_path / f".recovery-inspect.{suffix}"
    root.mkdir(mode=0o700)
    return root


def archive_fixture(tmp_path: Path, size: int = 2 * bounded.WINDOW_BYTES + 107):
    path = tmp_path / "archive.tgz"
    body = (b"inspection-evidence\n" * (size // 20 + 1))[:size]
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo("data")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o750
        directory.mtime = 1_700_000_000
        archive.addfile(directory)
        for name, content, mode in (
            ("large.bin", body, 0o640),
            ("small.bin", b"unaligned small file\n", 0o600),
            ("empty", b"", 0o440),
        ):
            member = tarfile.TarInfo(f"data/{name}")
            member.size, member.mode, member.mtime = len(content), mode, 1_700_000_001
            archive.addfile(member, io.BytesIO(content))
    return path, body


def test_bounded_extraction_and_hash_match_original_bytes_and_metadata(tmp_path):
    archive, body = archive_fixture(tmp_path)
    baseline = tmp_path / "baseline"
    migration.extract_validated_tar(
        archive, baseline, expected_roots=frozenset({"data"})
    )
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, (archive,)) as policy:
        migration.extract_validated_tar(
            archive,
            private / "restored",
            expected_roots=frozenset({"data"}),
            inspection_io=policy,
        )
        actual = private / "restored"
        assert migration.hash_tree(actual, inspection_io=policy) == migration.hash_tree(
            baseline
        )
        assert (
            policy.sha256_file(actual / "data/large.bin")
            == hashlib.sha256(body).hexdigest()
        )
        for original in baseline.rglob("*"):
            restored = actual / original.relative_to(baseline)
            left, right = original.stat(), restored.stat()
            assert (
                stat.S_IMODE(left.st_mode),
                left.st_uid,
                left.st_gid,
                left.st_mtime_ns,
            ) == (
                stat.S_IMODE(right.st_mode),
                right.st_uid,
                right.st_gid,
                right.st_mtime_ns,
            )


def test_private_writes_are_synced_before_bounded_page_aligned_advice(
    tmp_path, monkeypatch
):
    archive, _ = archive_fixture(tmp_path)
    private = scratch(tmp_path)
    events = []
    real_sync = getattr(os, "fdatasync", os.fsync)

    def sync(fd):
        real_sync(fd)
        events.append(("sync", os.fstat(fd).st_ino))

    def advise(fd, offset, length, advice):
        events.append(("advice", os.fstat(fd).st_ino, offset, length, advice))

    monkeypatch.setattr(os, "fdatasync", sync, raising=False)
    monkeypatch.setattr(os, "posix_fadvise", advise, raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    with bounded.InspectionIO(private, (archive,)) as policy:
        migration.extract_validated_tar(
            archive,
            private / "restored",
            expected_roots=frozenset({"data"}),
            inspection_io=policy,
        )
    inode = (private / "restored/data/large.bin").stat().st_ino
    writes = [event for event in events if event[1] == inode]
    advice = [event for event in writes if event[0] == "advice"]
    assert len(advice) >= 2
    page = os.sysconf("SC_PAGE_SIZE")
    for index, event in enumerate(writes):
        if event[0] == "advice":
            assert index and writes[index - 1][0] == "sync"
            assert event[2] % page == event[3] % page == 0
            assert event[3] <= bounded.WINDOW_BYTES


@pytest.mark.parametrize(
    "kind", ["outside", "file_symlink", "parent_symlink", "hardlink", "fifo"]
)
def test_live_or_aliased_files_never_receive_cache_advice(tmp_path, monkeypatch, kind):
    private = scratch(tmp_path)
    live = tmp_path / "live"
    live.mkdir()
    target = live / "database"
    target.write_bytes(b"live data" * 1000)
    events = []
    monkeypatch.setattr(
        os, "posix_fadvise", lambda *args: events.append(args), raising=False
    )
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    with bounded.InspectionIO(private, ()) as policy:
        path = private / "target"
        if kind == "outside":
            path = target
        elif kind == "file_symlink":
            path.symlink_to(target)
        elif kind == "parent_symlink":
            path.symlink_to(live, target_is_directory=True)
            path /= "database"
        elif kind == "hardlink":
            os.link(target, path)
        else:
            os.mkfifo(path)
        with pytest.raises(OSError):
            policy.sha256_file(path)
    assert events == []
    assert target.read_bytes() == b"live data" * 1000


def test_replaced_root_or_archive_is_rejected(tmp_path):
    archive, _ = archive_fixture(tmp_path, 100)
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, (archive,)) as policy:
        archive.rename(tmp_path / "retained.tgz")
        archive.write_bytes(b"replacement")
        with pytest.raises(bounded.InspectionIOError, match="identity changed"):
            policy.sha256_file(archive)
        private.rename(tmp_path / "retained-root")
        private.mkdir(mode=0o700)
        (private / "file").write_bytes(b"replacement")
        with pytest.raises(bounded.InspectionIOError, match="root identity"):
            policy.sha256_file(private / "file")


@pytest.mark.parametrize("error", [None, errno.EOPNOTSUPP])
def test_explicit_advisory_fallback_preserves_hash(
    tmp_path, monkeypatch, caplog, error
):
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, ()) as policy:
        path = private / "file"
        body = b"f" * (bounded.WINDOW_BYTES + 1)
        path.write_bytes(body)
        if error is None:
            policy._advice_supported = False
        else:
            policy._advice_supported = True

            def unsupported(*args):
                raise OSError(error, "unsupported")

            monkeypatch.setattr(os, "posix_fadvise", unsupported, raising=False)
            monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
        assert policy.sha256_file(path) == hashlib.sha256(body).hexdigest()
        assert policy.sha256_file(path) == hashlib.sha256(body).hexdigest()
        assert caplog.text.count("cache advice unavailable") == 1


def test_real_io_failure_is_not_treated_as_platform_fallback(tmp_path, monkeypatch):
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, ()) as policy:
        path = private / "file"
        path.write_bytes(b"f" * bounded.WINDOW_BYTES)
        policy._advice_supported = True
        descriptors = []

        def failure(*args):
            descriptors.append(args[0])
            raise OSError(errno.EIO, "disk failure")

        monkeypatch.setattr(os, "posix_fadvise", failure, raising=False)
        monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
        with pytest.raises(OSError, match="disk failure"):
            policy.sha256_file(path)
        assert descriptors
        for descriptor in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(descriptor)
            assert closed.value.errno == errno.EBADF


def test_existing_archive_rejection_precedes_any_extraction(tmp_path):
    path = tmp_path / "unsafe.tgz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("../escape")
        archive.addfile(member)
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, (path,)) as policy:
        with pytest.raises(migration.MigrationContractError, match="canonical"):
            migration.extract_validated_tar(
                path,
                private / "restored",
                expected_roots=frozenset({"data"}),
                inspection_io=policy,
            )
    assert not (tmp_path / "escape").exists()
    assert not (private / "restored").exists()


def test_sparse_payload_and_holes_are_preserved_with_bounded_writes(tmp_path):
    private = scratch(tmp_path)
    member = tarfile.TarInfo("sparse")
    member.size, member.offset_data = 3 * bounded.WINDOW_BYTES, 0
    member.sparse = [(100, 3), (2 * bounded.WINDOW_BYTES, 4)]

    class Archive:
        fileobj = io.BytesIO(b"abcdefg")

    with bounded.InspectionIO(private, ()) as policy:
        policy.makefile(Archive(), member, str(private / "sparse"))
    with (private / "sparse").open("rb") as handle:
        assert handle.read(103) == b"\0" * 100 + b"abc"
        handle.seek(2 * bounded.WINDOW_BYTES)
        assert handle.read(4) == b"defg"
    assert (private / "sparse").stat().st_size == member.size


@pytest.mark.parametrize("corrupt", [False, True])
def test_sqlite_validation_closes_reader_before_private_cache_release(
    tmp_path, monkeypatch, corrupt
):
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, ()) as policy:
        path = private / "seiche.sqlite"
        if corrupt:
            path.write_bytes(b"invalid sqlite database")
        else:
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE observations(value TEXT)")
            connection.commit()
            connection.close()
        connect = sqlite3.connect
        readers = []

        def tracked_connect(*args, **kwargs):
            reader = connect(*args, **kwargs)
            readers.append(reader)
            return reader

        monkeypatch.setattr(sqlite3, "connect", tracked_connect)
        if corrupt:
            with pytest.raises(sqlite3.DatabaseError):
                migration._validate_sqlite(path)
        else:
            migration._validate_sqlite(path)
            policy.release_audited_file(path)
        assert readers
        for reader in readers:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                reader.execute("SELECT 1")
