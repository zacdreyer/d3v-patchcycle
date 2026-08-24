from __future__ import annotations

import pytest

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir():
    return FIXTURES


@pytest.fixture()
def fake_bin(tmp_path):
    """Executable-looking files for APT provider construction."""
    bindir = tmp_path / "fixture-bin"
    bindir.mkdir(exist_ok=True)
    for name in ("apt-get", "dpkg", "dpkg-query", "apt-cache"):
        (bindir / name).write_text("#!fixture\n")
    return bindir


@pytest.fixture(autouse=True)
def _default_apt_binaries(fake_bin, monkeypatch):
    """All tests get resolvable apt binaries unless they override search_paths."""
    monkeypatch.setattr("patchcycle.providers.apt._DEFAULT_SEARCH_PATHS", (str(fake_bin),))


@pytest.fixture()
def fake_dnf_bin(tmp_path):
    """Executable-looking files for DNF provider construction."""
    bindir = tmp_path / "dnf-bin"
    bindir.mkdir()
    for name in ("dnf", "rpm"):
        (bindir / name).write_text("#!fixture\n")
    return bindir
