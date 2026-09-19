"""Build checksummed portable and per-OS installer bundles from release wheels."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path

PLATFORMS = {
    "ubuntu-22.04-x86_64": "ubuntu",
    "ubuntu-24.04-x86_64": "ubuntu",
    "debian-12-x86_64": "debian",
    "debian-13-x86_64": "debian",
    "rocky-9-x86_64": "rocky",
    "almalinux-9-x86_64": "almalinux",
    "fedora-44-x86_64": "fedora",
}


def checksum(path: Path) -> str:
    return f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    destination = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "dist"
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheel = destination / f"d3v_patchcycle-{version}-py3-none-any.whl"
    sdist = destination / f"d3v_patchcycle-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise SystemExit("Build the matching wheel and sdist before packaging")
    archives = []
    for platform, distro in {"portable": None, **PLATFORMS}.items():
        bundle = destination / f"d3v-patchcycle-{version}-{platform}"
        if bundle.exists():
            shutil.rmtree(bundle)
        bundle.mkdir()
        files = [
            wheel,
            root / "LICENSE",
            root / "INSTALL.md",
            root / "docs/operations/operations.md",
            root / "docs/specifications/configuration.md",
            root / "install/install-common.sh",
            root / "install/install.py",
        ]
        files.extend(
            sorted((root / "install").glob("install-*.sh"))
            if distro is None
            else [root / f"install/install-{distro}.sh"]
        )
        for path in files:
            shutil.copy2(path, bundle / path.name)
        shutil.copy2(root / "INSTALL.md", bundle / "README.md")
        (bundle / "SHA256SUMS.txt").write_text(
            "".join(checksum(p) for p in sorted(bundle.iterdir())), encoding="utf-8"
        )
        archive = Path(shutil.make_archive(str(bundle), "zip", bundle))
        with zipfile.ZipFile(archive) as stream:
            expected = {wheel.name, "README.md", "INSTALL.md", "install.py", "SHA256SUMS.txt"}
            if stream.testzip() is not None or not expected <= set(stream.namelist()):
                raise SystemExit("Bundle integrity/content check failed")
        archives.append(archive)
    checksums = "".join(checksum(p) for p in (wheel, sdist, *archives))
    (destination / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    print(checksums, end="")


if __name__ == "__main__":
    main()
