"""Release bundles must be complete; bootstrap must fail before unsafe writes."""

import hashlib
import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def bootstrap_module():
    spec = importlib.util.spec_from_file_location("release_bootstrap", ROOT / "install/install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_os_bundles_have_matching_checksums_and_installers(tmp_path):
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    wheel = tmp_path / f"d3v_patchcycle-{version}-py3-none-any.whl"
    wheel.write_bytes(b"fixture wheel")
    (tmp_path / f"d3v_patchcycle-{version}.tar.gz").write_bytes(b"fixture sdist")
    subprocess.run(  # noqa: S603 - local packaging tool, no host changes
        [sys.executable, str(ROOT / "tools/package_bundle.py"), str(tmp_path)], check=True
    )
    archives = list(tmp_path.glob("*.zip"))
    assert len(archives) == 8
    for archive in archives:
        with zipfile.ZipFile(archive) as stream:
            names = stream.namelist()
            assert {"INSTALL.md", "install.py", "install-common.sh", wheel.name} <= set(names)
            assert any(
                name.startswith("install-") and name != "install-common.sh" for name in names
            )
            for line in stream.read("SHA256SUMS.txt").decode().splitlines():
                digest, name = line.split("  ")
                assert hashlib.sha256(stream.read(name)).hexdigest() == digest
    for line in (tmp_path / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest


def test_bootstrap_refuses_corrupt_wheel_before_creating_paths(tmp_path, monkeypatch):
    module = bootstrap_module()
    monkeypatch.setattr(module, "__file__", str(tmp_path / "install.py"))
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    (tmp_path / "d3v_patchcycle-fixture-py3-none-any.whl").write_bytes(b"corrupted")
    (tmp_path / "SHA256SUMS.txt").write_text("0" * 64 + "  wheel.whl\n")
    target = tmp_path / "must-not-exist"
    monkeypatch.setattr(module, "ROOT", target)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        module.main()
    assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX installation ownership checks")
def test_bootstrap_rejects_symlink_into_writable_parent(tmp_path, monkeypatch):
    module = bootstrap_module()
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    alias = tmp_path / "alias"
    alias.symlink_to(unsafe, target_is_directory=True)
    original = Path.stat

    def trusted_tmp(path, **kwargs):
        info = original(path, **kwargs)
        if path == Path("/tmp"):
            return os.stat_result((info.st_mode & ~0o022, *info[1:]))
        return info

    monkeypatch.setattr(Path, "stat", trusted_tmp)
    with pytest.raises(RuntimeError, match=str(unsafe)):
        module.trusted_parent(alias / "new-install")
