"""Shared L3 container-test plumbing.

Two real bugs this fixes (found when CI first ran):
1. The container images ship Python older than the >=3.11 floor
   (ubuntu:22.04→3.10, rocky/alma:9→3.9). Each image installs a suitable
   interpreter via its native packages (deadsnakes for ubuntu:22.04,
   python3.11 from AppStream for EL9).
2. CI filtered tests with `-k "<image>"`, but image names contain a colon,
   which pytest's -k expression parser rejects. Parametrized IDs use a
   colon-free alias instead.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_DOCKER = shutil.which("docker")
docker_available = (
    _DOCKER is not None
    and subprocess.run(  # noqa: S603 - fixed argv
        [_DOCKER, "info"], capture_output=True, timeout=15, check=False
    ).returncode
    == 0
)


def image_alias(image: str) -> str:
    """Colon/dot-free alias safe for pytest -k and test IDs."""
    return image.replace(":", "_").replace(".", "_").replace("/", "_")


# Per-image interpreter bootstrap. Each produces $PY (3.11+) and installs the
# package from the read-only mounted repo.
_BOOTSTRAP = {
    "apt": (
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update -qq; "
        "apt-get install -y -qq python3 python3-venv python3-pip"
    ),
    "apt-22.04": (
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update -qq; "
        "apt-get install -y -qq software-properties-common; "
        "add-apt-repository -y ppa:deadsnakes/ppa; "
        "apt-get update -qq; "
        "apt-get install -y -qq python3.11 python3.11-venv"
    ),
    "dnf": "dnf install -y -q python3.11 python3.11-pip",
}

# The interpreter each image ends up with.
_PYTHON = {
    "ubuntu:22.04": "python3.11",
    "ubuntu:24.04": "python3",
    "debian:12": "python3",
    "debian:13": "python3",
    "rockylinux:9": "python3.11",
    "almalinux:9": "python3.11",
    "fedora:44": "python3",
}


def bootstrap_for(image: str) -> str:
    if image.startswith("fedora:"):
        return "dnf install -y -q python3 python3-pip"
    family = "apt" if image.startswith(("ubuntu", "debian")) else "dnf"
    if image == "ubuntu:22.04":
        return _BOOTSTRAP["apt-22.04"]
    return _BOOTSTRAP[family]


def python_for(image: str) -> str:
    return _PYTHON[image]


def run_in_container(
    image: str, script: str, timeout: int = 900
) -> subprocess.CompletedProcess[str]:
    assert _DOCKER is not None
    # Copy the mounted (read-only) source to a writable location: setuptools'
    # egg_info must write build artifacts, which fails on a ro mount. Copying
    # also keeps the host working tree untouched.
    inner = (
        "set -eu; mkdir /work; "
        "cp -r /src/src /src/tests /src/pyproject.toml /src/README.md /src/LICENSE /work; "
        "cd /work; " + script
    )
    name = f"patchcycle-l3-{uuid.uuid4().hex}"
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, test-controlled image
            [
                _DOCKER,
                "run",
                "--rm",
                "--name",
                name,
                "-v",
                f"{REPO}:/src:ro",
                image,
                "sh",
                "-c",
                inner,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        # A timed-out Docker client does not necessarily stop its container.
        subprocess.run(  # noqa: S603 - only the generated disposable fixture name
            [_DOCKER, "rm", "--force", name],
            capture_output=True,
            timeout=30,
            check=False,
        )


container_marks = [
    pytest.mark.container,
    pytest.mark.skipif(not docker_available, reason="docker daemon unavailable"),
]
