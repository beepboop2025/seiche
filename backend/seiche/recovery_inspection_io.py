"""Bounded cache treatment for one private recovery inspection.

Cache advice is advisory, never an integrity or continuity proof. Ordinary
migration and live database reads do not use this policy. Descriptor-relative
opens confine it to the new inspection directory and named snapshot archives.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import io
import logging
import os
from pathlib import Path
import stat
import tarfile
from typing import BinaryIO, Iterator

LOG = logging.getLogger(__name__)
CHUNK_BYTES = 1024 * 1024
WINDOW_BYTES = 16 * CHUNK_BYTES


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
        if not self._fallback_reported:
            LOG.warning(
                "Recovery inspection cache advice unavailable; bounded copying "
                "and private write synchronization remain active, cache "
                "eviction is not established"
            )
            self._fallback_reported = True

    @contextmanager
    def reader(self, path: Path) -> Iterator[BinaryIO]:
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
        with self._open_scratch(path) as handle:
            self._sync_discard(handle, 0, os.fstat(handle.fileno()).st_size)

    def makefile(self, archive: tarfile.TarFile, member: tarfile.TarInfo, path: str):
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
                self._sync_discard(target, window_start, target.tell())
            if member.sparse is not None:
                target.truncate(member.size)
                target.flush()
                getattr(os, "fdatasync", os.fsync)(target.fileno())

    def _sync_discard(self, handle: BinaryIO, start: int, end: int) -> None:
        handle.flush()
        # Synchronization supplies backpressure before releasing dirty pages.
        getattr(os, "fdatasync", os.fsync)(handle.fileno())
        self._discard(handle, start, end)

    def close(self) -> None:
        if self._root_fd >= 0:
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
