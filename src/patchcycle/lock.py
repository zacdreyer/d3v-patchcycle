"""Single-instance execution lock (architecture §7).

Exclusive, non-blocking, kernel-released-on-death: ``flock`` on POSIX,
``msvcrt.locking`` on Windows (dev/CI hosts only — production targets are
POSIX). Stale lock files can never block a future run because the lock lives
in the open file description, not the file's existence.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import TracebackType
from typing import IO

from patchcycle.errors import LockHeldError
from patchcycle.secure_io import check_directory, check_file

if sys.platform == "win32":  # pragma: no cover - platform specific
    import msvcrt
else:
    import fcntl


class ExecutionLock:
    """Context manager holding the PatchCycle execution lock."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fh: IO[str] | None = None

    def acquire(self) -> ExecutionLock:
        # O_NOFOLLOW: refuse to lock through a planted symlink (T6).
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            existing_parent = self.lock_path.parent
            while not existing_parent.exists() and not existing_parent.is_symlink():
                existing_parent = existing_parent.parent
            check_directory(existing_parent)
            self.lock_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            check_directory(self.lock_path.parent)
            if self.lock_path.exists() or self.lock_path.is_symlink():
                check_file(self.lock_path)
            fd = os.open(self.lock_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (
                os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o077)
            ):
                os.close(fd)
                raise OSError("unsafe lock file owner, type or mode")
        except OSError as exc:
            raise LockHeldError(f"cannot open lock file {self.lock_path}: {exc}") from exc
        fh = os.fdopen(fd, "r+")
        try:
            if sys.platform == "win32":  # pragma: no cover - platform specific
                # Lock one byte at offset 0; the byte must exist first.
                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    fh.write(" ")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            holder = self._read_holder()
            raise LockHeldError(
                f"another PatchCycle instance is running (lock: {self.lock_path}"
                f"{', held by pid ' + holder if holder else ''})"
            ) from exc
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()}\n")
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is not None:
            try:
                if sys.platform == "win32":  # pragma: no cover - platform specific
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def _read_holder(self) -> str:
        try:
            for line in self.lock_path.read_text().splitlines():
                if line.startswith("pid="):
                    return line[4:].strip()
        except OSError:
            pass
        return ""

    def __enter__(self) -> ExecutionLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
