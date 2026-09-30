"""Bounded cache treatment for one private recovery inspection.

Cache advice is advisory, never an integrity or continuity proof. Ordinary
migration and live database reads do not use this policy. Descriptor-relative
opens confine it to the new inspection directory and named snapshot archives.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import io
import logging
import os
from pathlib import Path
import stat
import sys
import tarfile
from threading import Lock
from typing import BinaryIO, Iterator

LOG = logging.getLogger(__name__)
CHUNK_BYTES = 1024 * 1024
WINDOW_BYTES = 16 * CHUNK_BYTES
SMALL_WORKERS = 4
SMALL_FD_LIMIT = 32
SMALL_PENDING_BYTES = 16 * CHUNK_BYTES
RANGE_WRITEBACK_FLAGS = 1 | 2 | 4  # WAIT_BEFORE | WRITE | WAIT_AFTER


def _linux_range_sync():
    """Use libc's 64-bit Linux wrapper, never architecture-specific syscall IDs."""
    if sys.platform != "linux" or ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    try:
        function = ctypes.CDLL(None, use_errno=True).sync_file_range
    except (AttributeError, OSError):
        return None
    function.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_int64, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function


class InspectionIOError(OSError):
    """The private inspection file authority or I/O operation failed."""


class InspectionIO:
    def __init__(self, scratch: Path, archives: tuple[Path, ...]):
        self.scratch = scratch.absolute()
        metadata = self.scratch.lstat()
        if (
            not self.scratch.name.startswith(".recovery-inspect.")
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or any(self.scratch.iterdir())
        ):
            raise InspectionIOError("inspection root is not new and private")
        self._root_fd = os.open(
            self.scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        self._root_identity = (metadata.st_dev, metadata.st_ino)
        self._archives: dict[Path, tuple[int, ...]] = {}
        self._advice_supported = hasattr(os, "posix_fadvise") and hasattr(
            os, "POSIX_FADV_DONTNEED"
        )
        self._fallback_reported = False
        self._fallback_lock = Lock()
        self._range_sync = _linux_range_sync()
        self._range_fallback_reported = False
        # Admission belongs to the single extraction thread. The executor's
        # queue is bounded by these retained futures, including running work.
        self._executor: ThreadPoolExecutor | None = None
        self._pending: dict[Future[None], int] = {}
        self._pending_bytes = 0
        self._small_error: BaseException | None = None
        try:
            self._check_root()
            for path in archives:
                path = path.absolute()
                with self._open_archive(path) as handle:
                    self._archives[path] = self._identity(os.fstat(handle.fileno()))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _identity(metadata: os.stat_result) -> tuple[int, ...]:
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise InspectionIOError("inspection requires a single-link regular file")
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_mode,
        )

    @staticmethod
    def _open_archive(path: Path) -> BinaryIO:
        # Pin the actual file descriptor, not a subsequent path-based cache call.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        return os.fdopen(fd, "rb", buffering=0)

    def _check_root(self) -> None:
        path_stat = self.scratch.lstat()
        descriptor_stat = os.fstat(self._root_fd)
        if any(
            (item.st_dev, item.st_ino) != self._root_identity
            or not stat.S_ISDIR(item.st_mode)
            or item.st_uid != os.geteuid()
            or stat.S_IMODE(item.st_mode) != 0o700
            for item in (path_stat, descriptor_stat)
        ):
            raise InspectionIOError("inspection root identity changed")

    def _open_scratch(self, path: Path, *, create: bool = False) -> BinaryIO:
        self._check_root()
        try:
            parts = path.absolute().relative_to(self.scratch).parts
        except ValueError as exc:
            raise InspectionIOError("file is outside inspection scratch") from exc
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise InspectionIOError("inspection path is not canonical")
        parent_fd = os.dup(self._root_fd)
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                os.close(parent_fd)
                parent_fd = next_fd
            flags = os.O_NOFOLLOW | os.O_NONBLOCK
            flags |= os.O_WRONLY | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            fd = os.open(parts[-1], flags, 0o600, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            self._identity(os.fstat(fd))
            return os.fdopen(fd, "wb" if create else "rb", buffering=0)
        except BaseException:
            os.close(fd)
            raise

    def _discard(self, handle: BinaryIO, start: int, end: int) -> None:
        # Whole consumed pages only; the final partial page may remain cached.
        page = os.sysconf("SC_PAGE_SIZE")
        start = ((start + page - 1) // page) * page
        end = (end // page) * page
        if end <= start:
            return
        self._identity(os.fstat(handle.fileno()))
        if self._advice_supported:
            try:
                os.posix_fadvise(
                    handle.fileno(), start, end - start, os.POSIX_FADV_DONTNEED
                )
                return
            except OSError as exc:
                if exc.errno not in {errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL}:
                    raise
                self._advice_supported = False
        with self._fallback_lock:
            if not self._fallback_reported:
                LOG.warning(
                    "Recovery inspection cache advice unavailable; bounded copying "
                    "and private write synchronization remain active, cache "
                    "eviction is not established"
                )
                self._fallback_reported = True

    @contextmanager
    def reader(self, path: Path) -> Iterator[BinaryIO]:
        self.drain()
        path = path.absolute()
        archive_identity = self._archives.get(path)
        handle = (
            self._open_archive(path)
            if archive_identity is not None
            else self._open_scratch(path)
        )
        with handle:
            identity = self._identity(os.fstat(handle.fileno()))
            if archive_identity is not None and identity != archive_identity:
                raise InspectionIOError("snapshot archive identity changed")
            with _WindowReader(handle, self) as reader:
                yield reader
                if self._identity(os.fstat(handle.fileno())) != identity:
                    raise InspectionIOError("inspection input changed during read")

    def sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with self.reader(path) as handle:
            while chunk := handle.read(CHUNK_BYTES):
                digest.update(chunk)
        return digest.hexdigest()

    def release_audited_file(self, path: Path) -> None:
        """Release only a private copy after its independent reader has closed."""
        self.drain()
        with self._open_scratch(path) as handle:
            self._sync_discard(handle, 0, os.fstat(handle.fileno()).st_size)

    def makefile(self, archive: tarfile.TarFile, member: tarfile.TarInfo, path: str):
        small = member.sparse is None and 0 <= member.size < CHUNK_BYTES
        page = os.sysconf("SC_PAGE_SIZE")
        charge = ((member.size + page - 1) // page) * page
        try:
            if small:
                self._admit_small(charge)
            else:
                self.drain()
            self._write_member(archive, member, path, small=small, charge=charge)
        except BaseException:
            # A failed extraction cannot leave asynchronous writers behind.
            self.close()
            raise

    def _write_member(self, archive, member, path, *, small: bool, charge: int):
        source = archive.fileobj
        source.seek(member.offset_data)
        with self._open_scratch(Path(path), create=True) as target:
            ranges = member.sparse if member.sparse is not None else [(0, member.size)]
            for offset, size in ranges:
                target.seek(offset)
                remaining = size
                window_start = offset
                while remaining:
                    chunk = source.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise tarfile.ReadError("unexpected end of data")
                    view = memoryview(chunk)
                    while view:
                        written = target.write(view)
                        if written is None or written <= 0:
                            raise InspectionIOError("inspection write made no progress")
                        view = view[written:]
                    remaining -= len(chunk)
                    if target.tell() - window_start >= WINDOW_BYTES:
                        self._sync_discard(target, window_start, target.tell())
                        window_start = target.tell()
                if not small:
                    self._sync_discard(target, window_start, target.tell())
            if member.sparse is not None:
                target.truncate(member.size)
                target.flush()
                getattr(os, "fdatasync", os.fsync)(target.fileno())
            if small:
                self._queue_small(target, member.size, charge)

    def _reap(self) -> None:
        for future in tuple(self._pending):
            if future.done():
                self._pending_bytes -= self._pending.pop(future)
                try:
                    future.result()
                except BaseException as exc:
                    if self._small_error is None:
                        self._small_error = exc
        if self._small_error is not None:
            raise self._small_error

    def _admit_small(self, charge: int) -> None:
        self._reap()
        # Reserve two descriptor slots before opening: the extraction handle
        # and its short-lived duplicate. After submission only one remains.
        while (
            len(self._pending) + 2 > SMALL_FD_LIMIT
            or self._pending_bytes + charge > SMALL_PENDING_BYTES
        ):
            wait(self._pending, return_when=FIRST_COMPLETED)
            self._reap()

    def _queue_small(self, target: BinaryIO, size: int, charge: int) -> None:
        target.flush()
        identity = self._identity(os.fstat(target.fileno()))[:3]
        if identity[2] != size:
            raise InspectionIOError("inspection output size changed")
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=SMALL_WORKERS, thread_name_prefix="inspection-sync"
            )
        descriptor = os.dup(target.fileno())
        try:
            future = self._executor.submit(self._sync_small, descriptor, identity)
        except BaseException:
            os.close(descriptor)
            raise
        self._pending[future] = charge
        self._pending_bytes += charge

    def _sync_small(self, descriptor: int, identity: tuple[int, ...]) -> None:
        # No pathname reopen: the descriptor pins the already-authorized inode.
        # TarFile may concurrently apply its original mode/mtime metadata.
        try:
            handle = os.fdopen(descriptor, "wb", buffering=0)
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            self._check_root()
            if self._identity(os.fstat(descriptor))[:3] != identity:
                raise InspectionIOError("inspection output identity changed")
            if not 0 <= identity[2] < CHUNK_BYTES:
                raise InspectionIOError("small inspection output size is invalid")
            self._sync_small_output(handle, 0, identity[2])
            if self._identity(os.fstat(descriptor))[:3] != identity:
                raise InspectionIOError("inspection output identity changed")

    def _sync_small_output(self, handle: BinaryIO, start: int, end: int) -> None:
        """Write back disposable small-copy data, without claiming durability.

        Only _sync_small calls this after authorizing a completed private inode.
        Backup archives, sealing, large/sparse writes and audited SQLite copies
        retain their existing fsync/fdatasync operations.
        """
        handle.flush()
        function = self._range_sync
        if function is not None and end > start:
            ctypes.set_errno(0)
            result = function(
                handle.fileno(), start, end - start, RANGE_WRITEBACK_FLAGS
            )
            if result == 0:
                self._discard(handle, start, end)
                return
            error = ctypes.get_errno() or errno.EIO
            if error not in {errno.ENOSYS, errno.EOPNOTSUPP}:
                # Includes EINTR: propagate InterruptedError rather than retrying
                # around deadline/signals. Python signal exceptions also escape.
                raise OSError(error, "private inspection range writeback failed")
            self._range_sync = None
        if function is None or self._range_sync is None:
            with self._fallback_lock:
                if not self._range_fallback_reported:
                    LOG.warning(
                        "Private inspection range writeback unavailable; "
                        "using fdatasync for disposable small copies"
                    )
                    self._range_fallback_reported = True
        self._sync_discard(handle, start, end)

    def drain(self) -> None:
        """Join all admitted work before a new phase, including on failure."""
        if self._pending:
            wait(self._pending)
        self._reap()

    def _sync_discard(self, handle: BinaryIO, start: int, end: int) -> None:
        handle.flush()
        # Synchronization supplies backpressure before releasing dirty pages.
        getattr(os, "fdatasync", os.fsync)(handle.fileno())
        self._discard(handle, start, end)

    def close(self) -> None:
        if self._root_fd >= 0:
            try:
                self.drain()
            finally:
                try:
                    if self._executor is not None:
                        self._executor.shutdown(wait=True)
                finally:
                    os.close(self._root_fd)
                    self._root_fd = -1

    def __enter__(self) -> InspectionIO:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _WindowReader(io.BufferedReader):
    def __init__(self, raw: BinaryIO, policy: InspectionIO):
        super().__init__(raw, buffer_size=CHUNK_BYTES)
        self._policy = policy
        self._released = 0

    def read(self, size: int = -1) -> bytes:
        body = super().read(size)
        end = self.tell()
        if end - self._released >= WINDOW_BYTES or not body:
            self._policy._discard(self.raw, self._released, end)
            self._released = end
        return body

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        position = super().seek(offset, whence)
        if position < self._released:
            self._released = position
        return position

    def close(self) -> None:
        try:
            if not self.closed:
                self._policy._discard(self.raw, self._released, self.tell())
        finally:
            super().close()


class InspectionTarFile(tarfile.TarFile):
    """Keep TarFile validation/metadata handling, bound only regular writes."""

    inspection_io: InspectionIO

    def makefile(self, tarinfo: tarfile.TarInfo, targetpath: str) -> None:
        self.inspection_io.makefile(self, tarinfo, targetpath)

    def extractall(self, *args, **kwargs) -> None:
        try:
            super().extractall(*args, **kwargs)
        finally:
            self.inspection_io.drain()
