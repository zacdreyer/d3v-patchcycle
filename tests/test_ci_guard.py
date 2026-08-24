"""Tests for the CI guard script (replaces the buggy grep one-liner)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).parents[1] / "tools" / "ci_guard.py"


def run_guard(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - test interpreter, fixed argv
        [sys.executable, str(GUARD)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


class TestGuard:
    def test_clean_tree_passes(self, tmp_path):
        root = make_tree(
            tmp_path,
            {
                "src/x/y.py": "import subprocess\nsubprocess.run(['/usr/bin/true'])\n",
                "tests/test_x.py": "subprocess.run(['python', '-c', 'pass'])\n",
            },
        )
        result = run_guard(root)
        assert result.returncode == 0

    def test_real_apt_call_flagged(self, tmp_path):
        root = make_tree(
            tmp_path,
            {
                "src/x/y.py": "subprocess.run(['apt-get', 'update'])\n",
                "tests/test_x.py": "subprocess.Popen(['dnf', 'check'])\n",
            },
        )
        result = run_guard(root)
        assert result.returncode == 1
        assert "apt-get" in result.stdout
        assert "dnf" in result.stdout

    def test_container_and_vm_tests_exempt(self, tmp_path):
        root = make_tree(
            tmp_path,
            {
                "tests/integration/test_c.py": "subprocess.run(['apt-get', 'update'])\n",
                "tests/vm/h.py": "subprocess.run(['systemctl', 'reboot'])\n",
            },
        )
        result = run_guard(root)
        assert result.returncode == 0

    def test_repo_tree_is_clean(self):
        """The actual repository must pass its own guard."""
        result = run_guard(Path(__file__).parents[1])
        assert result.returncode == 0, result.stdout
