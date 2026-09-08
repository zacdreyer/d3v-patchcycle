"""Build/verify a disposable VM update that restores package-owned SUID mode."""

import json
import stat
import subprocess
import sys
import time
from pathlib import Path


def command(*argv: str) -> str:
    return subprocess.check_output(list(argv), text=True)  # noqa: S603


def main() -> None:
    if not Path("/var/lib/patchcycle-l4/disposable").is_file():
        raise RuntimeError("requires the explicit disposable L4 guest marker")
    if sys.argv[1] == "verify-archive":
        state = Path("/var/lib/d3v-patchcycle/state.json")
        for _ in range(30):
            if not state.exists():
                break
            time.sleep(1)
        assert not state.exists(), "cycle did not become idle"
        report = json.loads(Path("/var/lib/patchcycle-l4/report.json").read_text())
        archive = Path("/var/lib/d3v-patchcycle/history") / (report["run_id"] + ".json")
        record = json.loads(archive.read_text())
        assert record["outcome"] == report["outcome"]
        assert record["notification_status"]["webhook"] == "sent"
        assert archive.with_suffix(".report.txt").read_text()
        print("PASS: idle, durable JSON/text archive and persisted webhook success")
        return
    if sys.argv[1] == "verify":
        assert (
            command("/usr/bin/dpkg-query", "-W", "-f=${Version}", "patchcycle-l4-fixture") == "2.0"
        )
        assert Path("/usr/share/patchcycle-l4-fixture/mode-probe").stat().st_mode & stat.S_ISUID
        print("PASS: package 1.0 -> 2.0, including required SUID restoration")
        return
    if sys.argv[1] == "verify-power-cut":
        status = command(
            "/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}", "patchcycle-l4-fixture"
        )
        assert status in ("half-configured", "unpacked"), status
        print("PASS: unexpected reboot reported; interrupted package was not silently repaired")
        return
    repo = Path("/opt/patchcycle-l4-repo")
    repo.mkdir(mode=0o755)
    for version in ("1.0", "2.0"):
        package = repo / f"package-{version}"
        control = package / "DEBIAN"
        control.mkdir(parents=True)
        (control / "control").write_text(
            f"Package: patchcycle-l4-fixture\nVersion: {version}\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\n"
            "Description: Disposable L4 permission fixture\n"
        )
        payload = package / "usr/share/patchcycle-l4-fixture"
        payload.mkdir(parents=True)
        (payload / "mode-probe").write_text("not executable content\n")
        if version == "2.0":
            postinst = control / "postinst"
            interruption = (
                "touch /var/lib/patchcycle-l4/transaction-started\nsync\nsleep 600\n"
                if sys.argv[1] == "prepare-power-cut"
                else ""
            )
            postinst.write_text(
                "#!/bin/sh\nset -e\n"
                + interruption
                + "chmod 4755 /usr/share/patchcycle-l4-fixture/mode-probe\n"
            )
            postinst.chmod(0o755)
        command("/usr/bin/dpkg-deb", "--build", str(package), str(repo / f"fixture-{version}.deb"))
    command("/usr/bin/dpkg", "-i", str(repo / "fixture-1.0.deb"))
    index = command("/usr/bin/dpkg-scanpackages", str(repo), "/dev/null")
    (repo / "Packages").write_text(index.replace(f"Filename: {repo}/", "Filename: "))
    Path("/etc/apt/sources.list.d/patchcycle-l4.list").write_text(
        f"deb [trusted=yes] file:{repo} ./\n"
    )


if __name__ == "__main__":
    main()
