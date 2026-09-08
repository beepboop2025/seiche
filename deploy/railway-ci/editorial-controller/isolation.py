"""Native process and file isolation reused from the reviewed LiquiLens collector."""

import ctypes
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import time

WRITER_UID = 65532
MAX_FILE_BYTES = 32 * 1024 * 1024

def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError(f"unsafe output type: {path.name}")
        if metadata.st_size > MAX_FILE_BYTES:
            raise ValueError(f"oversized output: {path.name}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError(f"oversized output: {path.name}")
        return content
    finally:
        os.close(fd)


def validate_json(path, content):
    def no_constant(value):
        raise ValueError(f"nonfinite JSON value: {value}")

    records = content.splitlines() if path.endswith(".jsonl") else [content]
    if not records or not any(record.strip() for record in records):
        raise ValueError(f"empty output: {path}")
    for record in records:
        if not record.strip():
            continue
        value = json.loads(record, parse_constant=no_constant)
        if not isinstance(value, (dict, list)):
            raise ValueError(f"invalid output envelope: {path}")


def git(mirror, env, *args, data=None):
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never"]
    if mirror is not None:
        command += ["--git-dir", str(mirror)]
    return subprocess.check_output(command + list(args), input=data, env=env,
                                   stdin=None, timeout=300)


def drop_privileges():
    # no_new_privs prevents setuid/file-capability programs restoring privilege.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    os.setgroups([])
    os.setgid(WRITER_UID)
    os.setuid(WRITER_UID)
    os.umask(0o022)


def stop_collectors():
    """Quiesce even orphaned UID-owned children before reading their output."""
    for _ in range(40):
        subprocess.run(["pkill", "-KILL", "-u", str(WRITER_UID)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        found = subprocess.run(["pgrep", "-u", str(WRITER_UID)],
                               stdout=subprocess.DEVNULL, check=False)
        if found.returncode == 1:
            return
        time.sleep(0.25)
    raise RuntimeError("collector processes did not quiesce")


