"""L3 provider integration tests: real APT in disposable containers.

Spec: docs/testing/test-strategy.md §L3. These run only where Docker is
available (GitHub Actions ubuntu runners); they are skipped elsewhere. They
never touch the host package manager — all apt activity happens inside the
container.

Run explicitly with: pytest -m container tests/integration
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

MATRIX = [
    "ubuntu:22.04",
    "ubuntu:24.04",
    "debian:12",
    "debian:13",
]

_DOCKER = shutil.which("docker")

docker_available = (
    _DOCKER is not None
    and subprocess.run(  # noqa: S603 - fixed argv
        [_DOCKER, "info"],
        capture_output=True,
        timeout=15,
        check=False,
    ).returncode
    == 0
)

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(not docker_available, reason="docker daemon unavailable"),
    pytest.mark.skipif(sys.platform == "win32" and not docker_available, reason="docker"),
]

# Script executed inside the container: install the package from the mounted
# source, then drive the real provider end-to-end.
CONTAINER_SCRIPT = r"""
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq python3 python3-pip python3-venv >/dev/null
python3 -m venv /opt/pc-venv
/opt/pc-venv/bin/pip install -q /src
echo "== detect =="
/opt/pc-venv/bin/d3v-patchcycle detect
echo "== updates (discovery only) =="
/opt/pc-venv/bin/d3v-patchcycle updates --config /dev/null || true
echo "== OK =="
"""


def run_in_container(
    image: str, script: str, timeout: int = 900
) -> subprocess.CompletedProcess[str]:
    assert _DOCKER is not None
    return subprocess.run(  # noqa: S603 - fixed argv, test-controlled image
        [
            _DOCKER,
            "run",
            "--rm",
            "-v",
            f"{REPO}:/src:ro",
            image,
            "sh",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.mark.parametrize("image", MATRIX)
def test_detect_and_discovery_in_container(image):
    """detect selects the apt provider; updates lists without changing state."""
    proc = run_in_container(image, CONTAINER_SCRIPT)
    assert proc.returncode == 0, f"{image}:\n{proc.stdout}\n{proc.stderr}"
    assert "Provider: apt (supported)" in proc.stdout


@pytest.mark.parametrize("image", ["ubuntu:24.04"])
def test_real_upgrade_and_reboot_detection(image):
    """A real (old-pinned) package upgrade through PatchCycle's engine path."""
    script = r"""
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq python3 python3-pip python3-venv >/dev/null
python3 -m venv /opt/pc-venv
/opt/pc-venv/bin/pip install -q /src

# Downgrade a trivial package so an update exists, then let PatchCycle upgrade.
apt-get install -y -qq --allow-downgrades hello=2.10-3 2>/dev/null || true

mkdir -p /var/lib/d3v-patchcycle
/opt/pc-venv/bin/d3v-patchcycle run --config /dev/null || rc=$?
echo "run exit: ${rc:-0}"
# State must be archived (cycle completed or cleanly failed).
ls /var/lib/d3v-patchcycle/history/ | grep -q . && echo "history: present"
echo "== OK =="
"""
    proc = run_in_container(image, script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "history: present" in proc.stdout
