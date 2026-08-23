"""Entry-point smoke test: python -m patchcycle."""

from __future__ import annotations

import subprocess
import sys


def test_python_m_patchcycle_version():
    proc = subprocess.run(  # noqa: S603 - spawning the test interpreter
        [sys.executable, "-m", "patchcycle", "version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert "d3v-patchcycle" in proc.stdout
