"""Real APT transaction fixture. Run ONLY inside a disposable Linux container."""

import json
import os
import subprocess
import sys
from pathlib import Path

from patchcycle.cli import main
from patchcycle.osdetect import OsDetector
from patchcycle.providers import select_provider


def command(*argv: str) -> str:
    return subprocess.check_output(list(argv), text=True)  # noqa: S603


def verify_native_lock(provider) -> None:
    script = (
        "import fcntl,sys; f=open('/var/lib/dpkg/lock-frontend','a'); "
        "fcntl.lockf(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()"
    )
    proc = subprocess.Popen(  # noqa: S603 - disposable-container lock fixture
        [sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        preflight = provider.preflight()
        assert not preflight.ok and preflight.kind == "pm-locked", preflight
    finally:
        proc.communicate("", timeout=5)


def main_fixture() -> None:
    if not Path("/.dockerenv").exists():
        raise RuntimeError("this destructive fixture requires a disposable Docker container")
    repo = Path("/opt/patchcycle-fixture-security")
    repo.mkdir()
    for version in ("1.0", "2.0"):
        package = repo / f"package-{version}"
        control = package / "DEBIAN"
        control.mkdir(parents=True)
        (control / "control").write_text(
            f"Package: patchcycle-fixture\nVersion: {version}\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\nDescription: Disposable audit fixture\n"
        )
        if version == "1.0":
            (control / "postinst").write_text("#!/bin/sh\nexit 1\n")
            (control / "postinst").chmod(0o755)
        command("/usr/bin/dpkg-deb", "--build", str(package), str(repo / f"fixture-{version}.deb"))
    failed_install = subprocess.run(  # noqa: S603 - deliberately failing fixture installation
        ["/usr/bin/dpkg", "-i", str(repo / "fixture-1.0.deb")], check=False
    )
    assert failed_install.returncode != 0
    provider = select_provider(OsDetector().detect())
    dirty = provider.preflight()
    assert not dirty.ok and dirty.kind == "interrupted-transaction", dirty
    assert "patchcycle-fixture" in dirty.detail
    Path("/var/lib/dpkg/info/patchcycle-fixture.postinst").write_text("#!/bin/sh\nexit 0\n")
    assert provider.repair_interrupted().ok
    verify_native_lock(provider)
    packages = command("/usr/bin/dpkg-scanpackages", str(repo), "/dev/null")
    # dpkg-scanpackages emits absolute filenames for an absolute search root.
    (repo / "Packages").write_text(packages.replace(f"Filename: {repo}/", "Filename: "))
    Path("/etc/apt/sources.list.d/patchcycle-fixture.list").write_text(
        f"deb [trusted=yes] file:{repo} ./\n"
    )
    config = Path("/opt/patchcycle-fixture.toml")
    config.write_text(
        '[maintenance]\nschedule="manual"\n[updates]\nstrategy="security"\n[reboot]\npolicy="never"\n'
    )
    config.chmod(0o600)
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    assert provider.refresh().ok
    command("/usr/bin/apt-mark", "hold", "patchcycle-fixture")
    held = [
        update for update in provider.list_updates("safe") if update.name == "patchcycle-fixture"
    ]
    assert len(held) == 1 and held[0].held, held
    command("/usr/bin/apt-mark", "unhold", "patchcycle-fixture")
    assert any(
        update.name == "patchcycle-fixture" and update.security
        for update in provider.list_updates("security")
    )
    code = main(["run", "--config", str(config)])
    assert code == 0, code
    version = command("/usr/bin/dpkg-query", "-W", "-f=${Version}", "patchcycle-fixture")
    assert version == "2.0", version
    records = list(Path("/var/lib/d3v-patchcycle/history").glob("*.json"))
    assert len(records) == 1, records
    report = json.loads(records[0].read_text())
    assert report["outcome"] in ("success", "success_with_warnings"), report
    assert report["packages_updated"] >= 1, report
    assert report["outstanding_updates"] == 0, report
    assert records[0].with_suffix(".report.txt").is_file()
    # Exercise a real full-strategy transaction on the same disposable package.
    command("/usr/bin/dpkg", "--unpack", str(repo / "fixture-1.0.deb"))
    Path("/var/lib/dpkg/info/patchcycle-fixture.postinst").write_text("#!/bin/sh\nexit 0\n")
    command("/usr/bin/dpkg", "--configure", "patchcycle-fixture")
    assert provider.apply_updates("full", provider.list_updates("full")).ok
    assert command("/usr/bin/dpkg-query", "-W", "-f=${Version}", "patchcycle-fixture") == "2.0"
    assert provider.verify().consistent and provider.verify().outstanding == 0
    print(
        "PASS: actual fixture upgraded 1.0 -> 2.0 and successful cycle archived; "
        "native lock, hold, interrupted repair, security and full strategies verified"
    )


if __name__ == "__main__":
    main_fixture()
