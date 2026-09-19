"""Package a built release into an installable bundle and checksum its files."""

from __future__ import annotations

import hashlib
import shutil
import sys
import tomllib
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    destination = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "dist"
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheel = destination / f"d3v_patchcycle-{version}-py3-none-any.whl"
    sdist = destination / f"d3v_patchcycle-{version}.tar.gz"
    if not wheel.is_file() or not sdist.is_file():
        raise SystemExit("Build the matching wheel and sdist before packaging")
    bundle = destination / f"d3v-patchcycle-{version}-portable"
    bundle.mkdir(exist_ok=True)
    for path in (
        wheel,
        root / "README.md",
        root / "LICENSE",
        root / "docs/operations/operations.md",
        root / "docs/specifications/configuration.md",
    ):
        shutil.copy2(path, bundle / path.name)
    archive = Path(shutil.make_archive(str(bundle), "zip", bundle))
    with zipfile.ZipFile(archive) as stream:
        expected = {wheel.name, "README.md", "LICENSE", "operations.md", "configuration.md"}
        if stream.testzip() is not None or not expected <= set(stream.namelist()):
            raise SystemExit("Portable bundle integrity/content check failed")
    checksums = []
    for path in (wheel, sdist, archive):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    (destination / "SHA256SUMS.txt").write_text("".join(checksums), encoding="utf-8")
    print("".join(checksums), end="")


if __name__ == "__main__":
    main()
