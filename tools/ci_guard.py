#!/usr/bin/env python3
"""CI guard: fail if any non-container test or source file invokes real
package-manager or reboot binaries via subprocess.

Spec: docs/testing/test-strategy.md §L3 ("CI safety rule"). This replaces the
previous grep one-liner, which had a bash quoting bug that broke the unit job.

Exit 0 = clean; exit 1 = violation(s) found (each printed).
"""

from __future__ import annotations

import re
from pathlib import Path

# subprocess.run/Popen invoked with a real PM/reboot binary as argv[0].
_PATTERN = re.compile(
    r"""subprocess\.(run|Popen)\(\s*\[?\s*['"]"""
    r"(apt-get|apt|dnf|zypper|pacman|apk|systemctl|shutdown|reboot)\b"
)

# Files allowed to reference these binaries: container-marked integration
# tests and the VM harness drive real systems by design. The guard itself and
# its test fixtures contain example strings, not real invocations.
_ALLOWED = re.compile(
    r"tests[\\/]integration[\\/]|tests[\\/]vm[\\/]"
    r"|tools[\\/]ci_guard\.py$|tests[\\/]test_ci_guard\.py$"
)


def scan(root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.as_posix()
        if _ALLOWED.search(rel):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()}")
    return violations


def main() -> int:
    violations = scan(Path("src")) + scan(Path("tests"))
    if violations:
        print("CI guard: real maintenance commands outside container/VM tests:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("CI guard: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
