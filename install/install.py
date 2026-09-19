"""Install a checksummed local wheel into a dedicated first-install environment."""

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/d3v-patchcycle")
CONFIG = Path("/etc/d3v-patchcycle/config.toml")
ENTRY = Path("/usr/local/bin/d3v-patchcycle")
STARTER = """# Review INSTALL.md before enabling maintenance.
[maintenance]
enabled = false
schedule = "manual"

[updates]
strategy = "safe"

[reboot]
policy = "notify_only"
existing_pending = "report_only"
"""


def trusted_parent(path):
    """Validate every existing ancestor before creating privileged paths."""
    for parent in reversed(path.absolute().parents):
        if not parent.exists() and not parent.is_symlink():
            continue
        resolved = parent.resolve(strict=True)
        for directory in (resolved, *resolved.parents):
            info = directory.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError(f"Untrusted installation directory: {directory}")


def run(argv):
    subprocess.run(argv, check=True)  # noqa: S603 - fixed commands, verified local wheel, no shell


def main():
    if os.geteuid() != 0 or sys.version_info < (3, 11):
        raise RuntimeError("Root and Python 3.11+ are required")
    bundle = Path(__file__).resolve().parent
    wheels = list(bundle.glob("d3v_patchcycle-*-py3-none-any.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            "Extract a release bundle first: exactly one wheel must accompany installer"
        )
    wheel = wheels[0]
    expected = hashlib.sha256(wheel.read_bytes()).hexdigest() + "  " + wheel.name
    if expected not in (bundle / "SHA256SUMS.txt").read_text().splitlines():
        raise RuntimeError("Wheel checksum mismatch; download and verify the release again")
    for target in (ROOT, CONFIG, ENTRY):
        trusted_parent(target)
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"Refusing to overwrite {target}; see upgrade instructions")
    os.umask(0o022)
    ROOT.mkdir(mode=0o755)
    run([sys.executable, "-I", "-m", "venv", str(ROOT / "venv")])
    python = ROOT / "venv/bin/python"
    run(
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "--isolated",
            "install",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
    )
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(STARTER)
    ENTRY.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    ENTRY.symlink_to(ROOT / "venv/bin/d3v-patchcycle")
    run([str(ENTRY), "detect"])
    if sys.argv[1] != "true":
        run([str(ENTRY), "install"])
    run([str(ENTRY), "config-check"])
    print("Installed. Maintenance is DISABLED; no updates or reboot were requested.")
    print(
        "Next: follow INSTALL.md to review configuration, preview updates, and enable maintenance."
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Installation stopped: {exc}. See INSTALL.md troubleshooting.") from exc
