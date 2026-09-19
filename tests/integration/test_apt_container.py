"""L3 provider integration tests: real APT in disposable containers.

Spec: docs/testing/test-strategy.md §L3. Runs only where Docker is available
(GitHub Actions ubuntu runners); skipped elsewhere. Never touches the host
package manager.

Run explicitly with: pytest -m container tests/integration
"""

from __future__ import annotations

import pytest

from .conftest import (
    bootstrap_for,
    container_marks,
    image_alias,
    python_for,
    run_in_container,
)

MATRIX = [
    "ubuntu:22.04",
    "ubuntu:24.04",
    "debian:12",
    "debian:13",
]

pytestmark = container_marks


def _install_cmd(image: str) -> str:
    py = python_for(image)
    # cwd is /work (writable copy of the repo; /src is read-only).
    return (
        f"{bootstrap_for(image)}; {py} -m venv /opt/pc-venv; /opt/pc-venv/bin/pip install -q /work"
    )


@pytest.mark.parametrize("image", MATRIX, ids=image_alias)
def test_detect_and_discovery_in_container(image):
    """detect selects the apt provider; updates lists without changing state."""
    script = (
        "set -eu\n"
        + _install_cmd(image)
        + '\necho "== detect =="\n'
        + "/opt/pc-venv/bin/d3v-patchcycle detect\n"
        + 'echo "== updates (discovery only) == "\n'
        + "install -m 600 /dev/null /opt/fixture-config.toml\n"
        + "/opt/pc-venv/bin/d3v-patchcycle updates --config /opt/fixture-config.toml\n"
        + 'echo "== OK =="\n'
    )
    proc = run_in_container(image, script)
    assert proc.returncode == 0, f"{image}:\n{proc.stdout}\n{proc.stderr}"
    assert "Provider: apt (supported)" in proc.stdout


@pytest.mark.parametrize("image", MATRIX, ids=image_alias)
def test_real_upgrade_and_reboot_detection(image):
    """A real (old-pinned) package upgrade through PatchCycle's engine path."""
    script = (
        "set -eu\n"
        + _install_cmd(image)
        + """
apt-get install -y -qq dpkg-dev
/opt/pc-venv/bin/python /work/tests/integration/apt_transaction.py
"""
    )
    proc = run_in_container(image, script)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS: actual fixture upgraded 1.0 -> 2.0" in proc.stdout
