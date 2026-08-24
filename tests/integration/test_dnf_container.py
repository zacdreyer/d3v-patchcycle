"""L3 provider integration: real DNF in disposable containers.

Runs only where Docker is available; skips elsewhere. Never touches the host
package manager.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

MATRIX = [
    "rockylinux:9",
    "almalinux:9",
    "fedora:41",
]

_DOCKER = shutil.which("docker")
docker_available = (
    _DOCKER is not None
    and subprocess.run(  # noqa: S603 - fixed argv
        [_DOCKER, "info"], capture_output=True, timeout=15, check=False
    ).returncode
    == 0
)

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(not docker_available, reason="docker daemon unavailable"),
]

CONTAINER_SCRIPT = r"""
set -eu
dnf install -y -q python3 python3-pip >/dev/null
python3 -m venv /opt/pc-venv 2>/dev/null || python3 -m pip install --quiet --target /opt/pc /src
PIP="/opt/pc-venv/bin/pip"
if [ ! -x "$PIP" ]; then PIP="python3 -m pip"; fi
$PIP install -q /src 2>/dev/null || python3 -m pip install -q /src
PC="/opt/pc-venv/bin/d3v-patchcycle"
[ -x "$PC" ] || PC="python3 -m patchcycle"
echo "== detect =="
$PC detect
echo "== OK =="
"""


def run_in_container(image: str, script: str, timeout: int = 900):
    return subprocess.run(  # noqa: S603 - fixed argv, test-controlled image
        [_DOCKER, "run", "--rm", "-v", f"{REPO}:/src:ro", image, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.mark.parametrize("image", MATRIX)
def test_detect_selects_dnf_provider(image):
    proc = run_in_container(image, CONTAINER_SCRIPT)
    assert proc.returncode == 0, f"{image}:\n{proc.stdout}\n{proc.stderr}"
    assert "Provider: dnf (supported)" in proc.stdout
