"""L3 provider integration: real DNF in disposable containers.

Runs only where Docker is available; skips elsewhere. Never touches the host
package manager.
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
    "rockylinux:9",
    "almalinux:9",
    "fedora:41",
]

pytestmark = container_marks


@pytest.mark.parametrize("image", MATRIX, ids=image_alias)
def test_detect_selects_dnf_provider(image):
    py = python_for(image)
    script = (
        "set -eu\n"
        + bootstrap_for(image)
        + f"\n{py} -m venv /opt/pc-venv\n"
        + "/opt/pc-venv/bin/pip install -q /work\n"
        + 'echo "== detect =="\n'
        + "/opt/pc-venv/bin/d3v-patchcycle detect\n"
        + 'echo "== OK =="\n'
    )
    proc = run_in_container(image, script)
    assert proc.returncode == 0, f"{image}:\n{proc.stdout}\n{proc.stderr}"
    assert "Provider: dnf (supported)" in proc.stdout
