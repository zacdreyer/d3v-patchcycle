from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def trusted_python():
    """A real hook interpreter with trusted ancestry, independent of pytest's venv."""
    from patchcycle.hooks import validate_hook_paths

    executable = Path("/usr/bin/python3") if os.name == "posix" else Path(sys.executable)
    executable = executable.resolve(strict=True)
    problems = validate_hook_paths(((str(executable),),))
    if problems:
        pytest.fail("unsafe Python hook fixture: " + "; ".join(problems))
    return str(executable)


@pytest.fixture()
def py_hook(trusted_python):
    """Build Python hook argv without changing the production path checks."""
    return lambda code: (trusted_python, "-c", code)


@pytest.fixture(autouse=True)
def private_fixture_files():
    """Fixtures containing config/state must match production's private modes.

    Tests of unsafe permissions explicitly chmod their malicious fixtures.
    """
    import os

    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture()
def fixtures_dir():
    return FIXTURES


@pytest.fixture()
def fake_bin(tmp_path):
    """Executable-looking files for APT provider construction."""
    import os

    bindir = tmp_path / "fixture-bin"
    bindir.mkdir(exist_ok=True)
    for name in ("apt-get", "dpkg", "dpkg-query", "apt-cache"):
        path = bindir / name
        path.write_text("#!fixture\n")
        if os.name == "posix":
            path.chmod(0o755)  # X_OK check fails on Linux without this
    return bindir


@pytest.fixture(autouse=True)
def _default_apt_binaries(fake_bin, monkeypatch):
    """All tests get resolvable apt binaries unless they override search_paths."""
    monkeypatch.setattr("patchcycle.providers.apt._DEFAULT_SEARCH_PATHS", (str(fake_bin),))


@pytest.fixture()
def fake_dnf_bin(tmp_path):
    """Executable-looking files for DNF provider construction."""
    import os

    bindir = tmp_path / "dnf-bin"
    bindir.mkdir()
    for name in ("dnf", "rpm"):
        path = bindir / name
        path.write_text("#!fixture\n")
        if os.name == "posix":
            path.chmod(0o755)
    return bindir
