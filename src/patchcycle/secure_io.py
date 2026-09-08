"""Privileged file checks shared by configuration, state and installation."""

import os
import stat
from pathlib import Path


def check_directory(path: Path) -> None:
    check_ancestors(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{path}: must be a directory, not a symlink")
    if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022):
        raise OSError(f"{path}: untrusted directory owner or permissions")


def check_file(path: Path, *, private: bool = True) -> None:
    """Refuse symlinks, special files and untrusted POSIX ownership/modes."""
    check_ancestors(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"{path}: must be a regular file, not a symlink or device")
    if os.name == "posix":
        if info.st_uid != 0:
            raise OSError(f"{path}: must be owned by root")
        forbidden = 0o077 if private else 0o022
        if info.st_mode & forbidden:
            raise OSError(f"{path}: unsafe file permissions")


def check_ancestors(path: Path) -> None:
    if os.name != "posix":
        return
    for parent in path.absolute().parents:
        info = parent.lstat()
        # Root-owned system aliases such as /var/run are safe only when their
        # resolved ancestry is trusted as well.
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != 0:
                raise OSError(f"{parent}: untrusted parent symlink")
            check_directory(parent.resolve(strict=True))
            continue
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if info.st_uid != 0 or (info.st_mode & 0o022 and not sticky_root):
            raise OSError(f"{parent}: untrusted parent directory")


def read_private(path: Path) -> bytes:
    check_file(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o077):
            raise OSError(f"{path}: unsafe owner or mode at open")
        return stream.read()
