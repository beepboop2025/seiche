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


def small_member(policy, path, size=8193):
    member = tarfile.TarInfo(path.name)
    member.size, member.offset_data = size, 0
    archive = type("Archive", (), {"fileobj": io.BytesIO(b"s" * size)})()
    policy.makefile(archive, member, str(path))


@pytest.mark.parametrize("size", [17, bounded.CHUNK_BYTES - 1])
def test_small_queue_applies_fd_and_page_rounded_byte_backpressure(
    tmp_path, monkeypatch, size
):
    import threading

    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    release = threading.Event()
    blocked = threading.Event()
    four_running = threading.Event()
    lock = threading.Lock()
    running = peak_running = 0
    descriptors = []
    errors = []
    original_sync = policy._sync_small_output
    original_admit = policy._admit_small

    def sync(handle, start, end):
        nonlocal running, peak_running
        with lock:
            descriptors.append(handle.fileno())
            running += 1
            peak_running = max(peak_running, running)
            if running == 4:
                four_running.set()
        try:
            assert release.wait(10)
            original_sync(handle, start, end)
        finally:
            with lock:
                running -= 1

    def admit(charge):
        if (
            len(policy._pending) + 2 > bounded.SMALL_FD_LIMIT
            or policy._pending_bytes + charge > bounded.SMALL_PENDING_BYTES
        ):
            blocked.set()
        original_admit(charge)
        assert len(policy._pending) + 2 <= bounded.SMALL_FD_LIMIT
        assert policy._pending_bytes + charge <= bounded.SMALL_PENDING_BYTES

    def produce():
        try:
            for number in range(40):
                small_member(policy, private / str(number), size)
            policy.drain()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(policy, "_sync_small_output", sync)
    monkeypatch.setattr(policy, "_admit_small", admit)
    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert four_running.wait(10)
        assert blocked.wait(10)
        page = os.sysconf("SC_PAGE_SIZE")
        charge = ((size + page - 1) // page) * page
        expected = min(
            bounded.SMALL_FD_LIMIT - 1, bounded.SMALL_PENDING_BYTES // charge
        )
        assert len(policy._pending) == expected
        assert policy._pending_bytes == expected * charge
        assert len(list(private.iterdir())) == expected
        assert producer.is_alive()
    finally:
        release.set()
        producer.join(10)
        policy.close()
    assert not producer.is_alive()
    assert not errors
    assert peak_running == bounded.SMALL_WORKERS
    assert not policy._pending and policy._pending_bytes == 0
    assert all(path.read_bytes() == b"s" * size for path in private.iterdir())
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_worker_failure_joins_other_work_closes_fds_and_stops_admission(
    tmp_path, monkeypatch
):
    import threading

    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    started = threading.Barrier(5)
    release = threading.Event()
    descriptors = []
    errors = []
    original_sync = policy._sync_small_output

    def sync(handle, start, end):
        descriptor = handle.fileno()
        descriptors.append(descriptor)
        started.wait(timeout=10)
        assert release.wait(10)
        if descriptor == descriptors[0]:
            raise OSError(errno.EIO, "small writeback failed")
        original_sync(handle, start, end)

    monkeypatch.setattr(policy, "_sync_small_output", sync)
    for number in range(4):
        small_member(policy, private / str(number))
    started.wait(timeout=10)

    def close():
        try:
            policy.close()
        except BaseException as exc:
            errors.append(exc)

    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert closer.is_alive()
    finally:
        release.set()
        closer.join(10)
    assert not closer.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], OSError)
    assert "small writeback failed" in str(errors[0])
    assert policy._root_fd == -1
    assert not policy._pending and policy._pending_bytes == 0
    assert all(not worker.is_alive() for worker in policy._executor._threads)
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(OSError, match="small writeback failed"):
        small_member(policy, private / "not-created")
    assert not (private / "not-created").exists()


def test_hash_waits_for_small_writeback_and_large_member_drains_first(
    tmp_path, monkeypatch
):
    import threading

    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    release = threading.Event()
    started = threading.Event()
    read_opened = threading.Event()
    result = []
    original_sync = policy._sync_small_output
    original_open = policy._open_scratch

    def sync(handle, start, end):
        if os.fstat(handle.fileno()).st_size < bounded.CHUNK_BYTES:
            started.set()
            assert release.wait(10)
        original_sync(handle, start, end)

    def opened(path, *, create=False):
        if not create:
            read_opened.set()
        return original_open(path, create=create)

    monkeypatch.setattr(policy, "_sync_small_output", sync)
    monkeypatch.setattr(policy, "_open_scratch", opened)
    small_member(policy, private / "small")
    assert started.wait(10)
    reader = threading.Thread(
        target=lambda: result.append(policy.sha256_file(private / "small"))
    )
    reader.start()
    try:
        assert not read_opened.wait(0.05)
    finally:
        release.set()
        reader.join(10)
    assert result == [hashlib.sha256(b"s" * 8193).hexdigest()]
    assert not policy._pending
    small_member(policy, private / "second")
    small_member(policy, private / "large", bounded.WINDOW_BYTES + 1)
    assert not policy._pending
    policy.close()


def test_extraction_error_drains_and_shutdown_closes_owned_handles(
    tmp_path, monkeypatch
):
    import threading

    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    release = threading.Event()
    started = threading.Event()
    errors = []
    original_sync = policy._sync_small_output

    def sync(handle, start, end):
        started.set()
        assert release.wait(10)
        original_sync(handle, start, end)

    monkeypatch.setattr(policy, "_sync_small_output", sync)
    small_member(policy, private / "first")
    assert started.wait(10)
    member = tarfile.TarInfo("truncated")
    member.size, member.offset_data = 10, 0
    archive = type("Archive", (), {"fileobj": io.BytesIO(b"x")})()

    def extract():
        try:
            policy.makefile(archive, member, str(private / "truncated"))
        except BaseException as exc:
            errors.append(exc)

    producer = threading.Thread(target=extract)
    producer.start()
    try:
        assert producer.is_alive()
    finally:
        release.set()
        producer.join(10)
    assert len(errors) == 1 and isinstance(errors[0], tarfile.ReadError)
    assert policy._root_fd == -1
    assert not policy._pending
    assert all(not worker.is_alive() for worker in policy._executor._threads)
    policy.close()


def test_small_worker_rejects_inode_alias_before_advice(tmp_path, monkeypatch):
    import threading

    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    release = threading.Event()
    started = threading.Event()
    advice = []
    original_worker = policy._sync_small

    def worker(descriptor, identity):
        started.set()
        assert release.wait(10)
        original_worker(descriptor, identity)

    monkeypatch.setattr(policy, "_sync_small", worker)
    monkeypatch.setattr(policy, "_discard", lambda *args: advice.append(args))
    small_member(policy, private / "file")
    assert started.wait(10)
    os.link(private / "file", private / "alias")
    release.set()
    with pytest.raises(bounded.InspectionIOError, match="single-link"):
        policy.close()
    assert advice == []
    assert policy._root_fd == -1


def test_archive_boundary_joins_small_workers_before_return(tmp_path, monkeypatch):
    private = scratch(tmp_path)
    archive, _ = archive_fixture(tmp_path, 8193)
    completed = []
    original_worker = bounded.InspectionIO._sync_small

    def worker(self, descriptor, identity):
        original_worker(self, descriptor, identity)
        completed.append(identity)

    monkeypatch.setattr(bounded.InspectionIO, "_sync_small", worker)
    with bounded.InspectionIO(private, (archive,)) as policy:
        migration.extract_validated_tar(
            archive,
            private / "restored",
            expected_roots=frozenset({"data"}),
            inspection_io=policy,
        )
        assert len(completed) == 3
        assert not policy._pending and policy._pending_bytes == 0


def test_queue_flushes_buffered_payload_before_dup_and_worker_identity(tmp_path):
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, ()) as policy:
        body = b"buffered private data"
        with policy._open_scratch(private / "file", create=True) as raw:
            with io.BufferedWriter(raw) as target:
                target.write(body)
                assert os.fstat(raw.fileno()).st_size == 0
                policy._admit_small(os.sysconf("SC_PAGE_SIZE"))
                policy._queue_small(target, len(body), os.sysconf("SC_PAGE_SIZE"))
        policy.drain()
        assert policy.sha256_file(private / "file") == hashlib.sha256(body).hexdigest()


def test_failed_executor_submission_closes_duplicate_and_root(tmp_path, monkeypatch):
    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    descriptors = []

    class BrokenExecutor:
        def submit(self, callback, descriptor, identity):
            descriptors.append(descriptor)
            raise RuntimeError("executor unavailable")

        def shutdown(self, *, wait):
            assert wait

    policy._executor = BrokenExecutor()
    with pytest.raises(RuntimeError, match="executor unavailable"):
        small_member(policy, private / "file")
    assert policy._root_fd == -1
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_parallel_unsupported_advice_is_reported_once(tmp_path, monkeypatch, caplog):
    import threading

    private = scratch(tmp_path)
    advice_barrier = threading.Barrier(4)

    def unavailable(*args):
        advice_barrier.wait(timeout=10)
        raise OSError(errno.EOPNOTSUPP, "unsupported")

    monkeypatch.setattr(os, "posix_fadvise", unavailable, raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", 4, raising=False)
    with bounded.InspectionIO(private, ()) as policy:
        for number in range(4):
            small_member(
                policy, private / str(number), 2 * os.sysconf("SC_PAGE_SIZE") + 1
            )
        policy.drain()
        assert caplog.text.count("cache advice unavailable") == 1


@pytest.mark.parametrize("unsupported", ["platform", "word_size", "symbol", "library"])
def test_range_wrapper_is_only_loaded_for_supported_64bit_linux(
    monkeypatch, unsupported
):
    import ctypes

    calls = []

    def loader(*args, **kwargs):
        calls.append((args, kwargs))
        if unsupported == "library":
            raise OSError("libc unavailable")
        return object()

    monkeypatch.setattr(
        bounded.sys, "platform", "darwin" if unsupported == "platform" else "linux"
    )
    monkeypatch.setattr(
        ctypes, "sizeof", lambda value: 4 if unsupported == "word_size" else 8
    )
    monkeypatch.setattr(ctypes, "CDLL", loader)
    assert bounded._linux_range_sync() is None
    assert len(calls) == (0 if unsupported in {"platform", "word_size"} else 1)
    if calls:
        assert calls[0] == ((None,), {"use_errno": True})


def test_range_wrapper_pins_libc_argument_and_return_types(monkeypatch):
    import ctypes

    class Function:
        pass

    function = Function()
    library = type("Libc", (), {"sync_file_range": function})()
    monkeypatch.setattr(bounded.sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "sizeof", lambda value: 8)
    monkeypatch.setattr(ctypes, "CDLL", lambda name, use_errno: library)
    assert bounded._linux_range_sync() is function
    assert function.argtypes == [
        ctypes.c_int,
        ctypes.c_int64,
        ctypes.c_int64,
        ctypes.c_uint,
    ]
    assert function.restype is ctypes.c_int


def test_completed_small_private_payload_uses_range_then_advice(tmp_path, monkeypatch):
    private = scratch(tmp_path)
    events = []
    with bounded.InspectionIO(private, ()) as policy:

        def writeback(fd, offset, size, flags):
            metadata = os.fstat(fd)
            assert metadata.st_size == size == 8193
            assert metadata.st_ino == (private / "file").stat().st_ino
            events.append(("writeback", offset, size, flags))
            return 0

        def forbidden_fsync(fd):
            raise AssertionError("successful range path fell through to fdatasync")

        monkeypatch.setattr(policy, "_range_sync", writeback)
        monkeypatch.setattr(os, "fdatasync", forbidden_fsync, raising=False)
        monkeypatch.setattr(
            policy,
            "_discard",
            lambda handle, start, end: events.append(("advice", start, end)),
        )
        small_member(policy, private / "file")
        policy.drain()
        assert events == [("writeback", 0, 8193, 7), ("advice", 0, 8193)]
    assert (private / "file").read_bytes() == b"s" * 8193


@pytest.mark.parametrize("error", [None, errno.ENOSYS, errno.EOPNOTSUPP])
def test_unsupported_range_uses_existing_fdatasync_before_advice(
    tmp_path, monkeypatch, caplog, error
):
    import ctypes

    private = scratch(tmp_path)
    events = []
    real_sync = getattr(os, "fdatasync", os.fsync)
    with bounded.InspectionIO(private, ()) as policy:

        def unsupported(*args):
            ctypes.set_errno(error)
            events.append("unsupported")
            return -1

        def sync(fd):
            real_sync(fd)
            events.append("fdatasync")

        monkeypatch.setattr(
            policy, "_range_sync", None if error is None else unsupported
        )
        monkeypatch.setattr(os, "fdatasync", sync, raising=False)
        monkeypatch.setattr(policy, "_discard", lambda *args: events.append("advice"))
        for number in range(2):
            small_member(policy, private / str(number))
            policy.drain()
        assert (
            events
            == ([] if error is None else ["unsupported"]) + ["fdatasync", "advice"] * 2
        )
        assert caplog.text.count("range writeback unavailable") == 1


@pytest.mark.parametrize(
    "error",
    [
        errno.EIO,
        errno.ENOSPC,
        errno.ENOMEM,
        errno.EBADF,
        errno.ESPIPE,
        errno.EINVAL,
        errno.EINTR,
    ],
)
def test_range_failures_propagate_without_fallback_or_advice_and_close(
    tmp_path, monkeypatch, error
):
    import ctypes

    private = scratch(tmp_path)
    policy = bounded.InspectionIO(private, ())
    descriptors = []
    forbidden = []

    def fail(fd, start, size, flags):
        descriptors.append(fd)
        ctypes.set_errno(error)
        return -1

    monkeypatch.setattr(policy, "_range_sync", fail)
    monkeypatch.setattr(
        os, "fdatasync", lambda *args: forbidden.append("fallback"), raising=False
    )
    monkeypatch.setattr(policy, "_discard", lambda *args: forbidden.append("advice"))
    small_member(policy, private / "file")
    with pytest.raises(OSError) as caught:
        policy.close()
    assert caught.value.errno == error
    if error == errno.EINTR:
        assert isinstance(caught.value, InterruptedError)
    assert forbidden == [] and len(descriptors) == 1
    assert policy._root_fd == -1 and not policy._pending
    assert all(not worker.is_alive() for worker in policy._executor._threads)
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_python_signal_exception_is_not_swallowed_or_retried(tmp_path, monkeypatch):
    policy = bounded.InspectionIO(scratch(tmp_path), ())
    calls = []

    def interrupted(*args):
        calls.append(args)
        raise KeyboardInterrupt("deadline signal")

    monkeypatch.setattr(policy, "_range_sync", interrupted)
    small_member(policy, policy.scratch / "file")
    with pytest.raises(KeyboardInterrupt, match="deadline signal"):
        policy.close()
    assert len(calls) == 1 and policy._root_fd == -1 and not policy._pending


def test_range_never_replaces_large_sparse_audit_or_archive_operations(
    tmp_path, monkeypatch
):
    private = scratch(tmp_path)
    archive, _ = archive_fixture(tmp_path, 100)
    real_sync = getattr(os, "fdatasync", os.fsync)
    synced = []
    with bounded.InspectionIO(private, (archive,)) as policy:

        def forbidden(*args):
            raise AssertionError("range writeback escaped the small private worker")

        def sync(fd):
            synced.append(os.fstat(fd).st_ino)
            real_sync(fd)

        monkeypatch.setattr(policy, "_range_sync", forbidden)
        monkeypatch.setattr(os, "fdatasync", sync, raising=False)
        small_member(policy, private / "large", bounded.CHUNK_BYTES)
        member = tarfile.TarInfo("sparse")
        member.size, member.offset_data, member.sparse = 1024, 0, [(50, 3)]
        source = type("Archive", (), {"fileobj": io.BytesIO(b"abc")})()
        policy.makefile(source, member, str(private / "sparse"))
        policy.release_audited_file(private / "large")
        assert (
            policy.sha256_file(archive)
            == hashlib.sha256(archive.read_bytes()).hexdigest()
        )
        with pytest.raises(bounded.InspectionIOError, match="outside"):
            policy.release_audited_file(archive)
    assert len(synced) == 4


def test_disposable_inspection_returns_only_identity_and_removes_private_copies(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from seiche import stateful_recovery as recovery

    bundle_root = tmp_path / "retained"
    bundle_root.mkdir()
    archives = [
        bundle_root / name
        for name in ("var-lib-seiche.tgz", "api-data.tgz", "palimpsest-china.tgz")
    ]
    for path in archives:
        path.write_bytes(b"retained immutable archive")
    parent = tmp_path / "inspections"
    parent.mkdir()
    calls = []
    policies = []

    def writeback(fd, offset, size, flags):
        candidates = list(parent.glob(".recovery-inspect.*/copy"))
        assert (
            len(candidates) == 1 and candidates[0].stat().st_ino == os.fstat(fd).st_ino
        )
        calls.append((offset, size, flags))
        return 0

    def restore(bundle, staging, *, inspection_io, agent_room_audit_out, **kwargs):
        policies.append(inspection_io)
        small_member(inspection_io, staging / "copy")
        value = inspection_io.sha256_file(staging / "copy")
        agent_room_audit_out["synthetic"] = True
        return "verified_head", {"synthetic": value}

    monkeypatch.setattr(bounded, "_linux_range_sync", lambda: writeback)
    monkeypatch.setattr(recovery.migration, "restore_filesystem_generation", restore)
    bundle = SimpleNamespace(root=bundle_root, schema=migration.BACKUP_SCHEMA)
    result = recovery._restored_filesystem_identity(bundle, scratch_parent=parent)
    assert result == (
        "verified_head",
        {"synthetic": hashlib.sha256(b"s" * 8193).hexdigest()},
        {"synthetic": True},
    )
    assert calls == [(0, 8193, 7)]
    assert list(parent.iterdir()) == []
    assert all(path.read_bytes() == b"retained immutable archive" for path in archives)
    assert policies[0]._root_fd == -1 and not policies[0]._pending


@pytest.mark.skipif(
    bounded.sys.platform != "linux"
    or bounded.ctypes.sizeof(bounded.ctypes.c_void_p) != 8,
    reason="Requires the actual64-bit Linux libc range-writeback interface",
)
def test_actual_linux_range_writeback_preserves_complete_small_files(
    tmp_path, monkeypatch
):
    private = scratch(tmp_path)
    with bounded.InspectionIO(private, ()) as policy:
        actual = policy._range_sync
        assert actual is not None, "Qualified Linux image must expose the libc wrapper"
        calls = []

        def observed(fd, start, length, flags):
            result = actual(fd, start, length, flags)
            assert result == 0, bounded.ctypes.get_errno()
            calls.append((start, length, flags))
            return result

        monkeypatch.setattr(policy, "_range_sync", observed)
        for number in range(20):
            small_member(policy, private / str(number), 16385 + number)
        policy.drain()
        assert len(calls) == 20 and all(
            start == 0 and flags == 7 for start, _, flags in calls
        )
        for number in range(20):
            assert (
                policy.sha256_file(private / str(number))
                == hashlib.sha256(b"s" * (16385 + number)).hexdigest()
            )
