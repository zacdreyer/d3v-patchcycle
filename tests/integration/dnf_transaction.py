"""Deterministic RPM update fixture; disposable Docker containers only."""

import json
import subprocess
from pathlib import Path

from patchcycle.cli import main
from patchcycle.osdetect import OsDetector
from patchcycle.providers import select_provider


def command(*argv: str) -> str:
    return subprocess.check_output(list(argv), text=True)  # noqa: S603


def main_fixture() -> None:
    if not Path("/.dockerenv").exists():
        raise RuntimeError("this fixture requires a disposable Docker container")
    root = Path("/opt/patchcycle-rpm-fixture")
    root.mkdir()
    spec = root / "fixture.spec"
    spec.write_text("""Name: patchcycle-fixture
Version: %{fixture_version}
Release: 1
Summary: Disposable readiness fixture
License: MIT
BuildArch: noarch
%description
Disposable readiness fixture.
%install
mkdir -p %{buildroot}/usr/share/patchcycle-fixture
echo %{version} > %{buildroot}/usr/share/patchcycle-fixture/version
%files
/usr/share/patchcycle-fixture/version
""")
    for version in ("1.0", "2.0"):
        command(
            "/usr/bin/rpmbuild",
            "-bb",
            "--define",
            f"_topdir {root}/build",
            "--define",
            f"fixture_version {version}",
            str(spec),
        )
    repo = root / "build/RPMS/noarch"
    command("/usr/bin/rpm", "-i", str(repo / "patchcycle-fixture-1.0-1.noarch.rpm"))
    command("/usr/bin/createrepo_c", str(repo))
    advisory = root / "updateinfo.xml"
    advisory.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<updates><update from="audit@example.invalid" status="stable" type="security" version="1">
<id>PATCHCYCLE-2026:0001</id><title>Disposable security fixture</title>
<issued date="2026-09-08 00:00:00"/><updated date="2026-09-08 00:00:00"/>
<description>Synthetic acceptance advisory.</description><severity>Important</severity>
<pkglist><collection short="patchcycle"><name>Disposable fixture</name>
<package name="patchcycle-fixture" version="2.0" release="1" epoch="0" arch="noarch">
<filename>patchcycle-fixture-2.0-1.noarch.rpm</filename>
</package></collection></pkglist></update></updates>
""")
    command("/usr/bin/modifyrepo_c", "--mdtype=updateinfo", str(advisory), str(repo / "repodata"))
    Path("/etc/yum.repos.d/patchcycle-fixture.repo").write_text(
        f"[patchcycle-fixture]\nname=Disposable fixture\nbaseurl=file://{repo}\nenabled=1\ngpgcheck=0\n"
    )
    config = root / "config.toml"
    config.write_text(
        '[maintenance]\nschedule="manual"\n[updates]\nstrategy="security"\n'
        '[reboot]\npolicy="never"\nexisting_pending="continue_then_reboot"\n'
    )
    config.chmod(0o600)
    command("/usr/bin/dnf", "versionlock", "add", "patchcycle-fixture")
    provider = select_provider(OsDetector().detect())
    assert provider.refresh().ok
    updates = provider.list_updates("safe")
    fixture = [u for u in updates if u.name == "patchcycle-fixture"]
    assert fixture and all(u.held for u in fixture), updates
    command("/usr/bin/dnf", "versionlock", "delete", "patchcycle-fixture")
    security = provider.list_updates("security")
    assert any(update.name == "patchcycle-fixture" and update.security for update in security), (
        security
    )
    code = main(["run", "--config", str(config)])
    assert code in (0, 6), code
    assert command("/usr/bin/rpm", "-q", "--qf", "%{VERSION}", "patchcycle-fixture") == "2.0"
    records = list(Path("/var/lib/d3v-patchcycle/history").glob("*.json"))
    assert len(records) == 1, records
    report = json.loads(records[0].read_text())
    assert report["outcome"] in ("success", "success_with_warnings", "manual_reboot_required"), (
        report
    )
    assert report["packages_updated"] >= 1 and report["outstanding_updates"] == 0, report
    assert report["error"] is None, report
    assert records[0].with_suffix(".report.txt").is_file()
    # Downgrade only this synthetic package, then prove a real full-strategy update.
    command("/usr/bin/rpm", "-U", "--oldpackage", str(repo / "patchcycle-fixture-1.0-1.noarch.rpm"))
    assert provider.apply_updates("full", provider.list_updates("full")).ok
    assert command("/usr/bin/rpm", "-q", "--qf", "%{VERSION}", "patchcycle-fixture") == "2.0"
    verification = provider.verify()
    assert verification.consistent and verification.outstanding == 0, verification
    print(
        "PASS: actual RPM fixture upgraded 1.0 -> 2.0 and cycle archived; "
        "versionlock, security and full strategies verified"
    )


if __name__ == "__main__":
    main_fixture()
