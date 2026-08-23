"""Operating-system detection (product spec FR-01; research §1).

Authoritative source on Linux: /etc/os-release (falling back to
/usr/lib/os-release, never combined) per os-release(5). The parser below
implements the spec's shell-compatible assignment grammar; on Python ≥ 3.10
POSIX hosts the stdlib ``platform.freedesktop_os_release`` reader is used
for the live system. ``uname`` is never used to pick a provider.
"""

from __future__ import annotations

import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from patchcycle.models import OsIdentity

_LINE_RE = re.compile(r"^([A-Z][A-Z_0-9]*)=(.*)$")
_OS_RELEASE_PATHS = (Path("/etc/os-release"), Path("/usr/lib/os-release"))


def parse_os_release(text: str) -> dict[str, str]:
    """Parse os-release content per os-release(5).

    Handles double/single quoting and backslash escapes; later duplicate keys
    win (shell semantics); comments, blank lines, and malformed lines are
    skipped; unknown fields are preserved but ignorable by callers.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        fields[key] = _unquote(raw)
    return fields


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        out: list[str] = []
        i = 1
        while i < len(raw) - 1:
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw) - 1:
                nxt = raw[i + 1]
                if nxt in ("\\", '"', "'", "$", "`"):
                    out.append(nxt)
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)
    return raw


def read_os_release(paths: tuple[Path, ...] = _OS_RELEASE_PATHS) -> dict[str, str] | None:
    """Read the first available os-release file; /etc wins; never combined."""
    for path in paths:
        try:
            return parse_os_release(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return None


def _detect_init() -> str:  # pragma: no cover - live-system probing (L3)
    if sys.platform == "darwin":
        return "launchd"
    if Path("/run/systemd/system").exists() or Path("/bin/systemctl").exists():
        return "systemd"
    if Path("/sbin/openrc").exists():
        return "openrc"
    return ""


def _sw_vers() -> dict[str, str]:  # pragma: no cover - macOS-only (Phase 8)
    """Read macOS version info via sw_vers (argv, no shell)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["/usr/bin/sw_vers"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return {}
    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


@dataclass(frozen=True)
class OsDetector:
    """Produces an OsIdentity. All inputs injectable for tests (L1)."""

    fields: dict[str, str] | None = None  # os-release content; None = live read
    system: str | None = None
    kernel: str | None = None
    arch: str | None = None
    init: str | None = None
    sw_vers: dict[str, str] | None = None  # macOS only
    extra: dict[str, str] = field(default_factory=dict)

    def detect(self) -> OsIdentity:
        system = self.system if self.system is not None else platform.system()
        kernel = self.kernel if self.kernel is not None else platform.release()
        arch = self.arch if self.arch is not None else platform.machine()

        if system == "Darwin":
            ver = self.sw_vers if self.sw_vers is not None else _sw_vers()
            version = ver.get("ProductVersion", "")
            name = ver.get("ProductName", "macOS")
            return OsIdentity(
                family="macos",
                os_id="macos",
                version_id=version,
                pretty_name=f"{name} {version}".strip(),
                arch=arch,
                kernel=kernel,
                init=self.init if self.init is not None else "launchd",
            )

        if system == "Linux":
            fields = self.fields if self.fields is not None else (read_os_release() or {})
            os_id = fields.get("ID", "linux")
            id_like = tuple(fields.get("ID_LIKE", "").split())
            return OsIdentity(
                family="linux",
                os_id=os_id,
                id_like=id_like,
                version_id=fields.get("VERSION_ID", ""),
                codename=fields.get("VERSION_CODENAME", ""),
                pretty_name=fields.get("PRETTY_NAME", fields.get("NAME", "Linux")),
                arch=arch,
                kernel=kernel,
                init=self.init if self.init is not None else _detect_init(),
            )

        pretty = f"{system} {kernel}".strip()
        return OsIdentity(
            family=f"unsupported:{system.lower()}",
            os_id=system.lower(),
            pretty_name=pretty,
            arch=arch,
            kernel=kernel,
            init=self.init if self.init is not None else "",
        )
