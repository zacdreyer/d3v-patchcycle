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
    "fedora:44",
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
        + "dnf install -y -q rpm-build createrepo_c dnf-plugins-core\n"
        + "dnf install -y -q 'dnf-command(versionlock)'\n"
        + "/opt/pc-venv/bin/python /work/tests/integration/dnf_transaction.py\n"
    )
    proc = run_in_container(image, script)
    assert proc.returncode == 0, f"{image}:\n{proc.stdout}\n{proc.stderr}"
    assert "Provider: dnf (supported)" in proc.stdout
    assert "PASS: actual RPM fixture upgraded 1.0 -> 2.0" in proc.stdout
