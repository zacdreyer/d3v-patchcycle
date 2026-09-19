"""Unknown native health results must not be declared healthy."""

from types import SimpleNamespace

import pytest

from patchcycle.health import _default_failed_units


@pytest.mark.parametrize("payload", ["{}", '[{"unit": 123}]', "null"])
def test_invalid_systemd_json_is_unknown_health(monkeypatch, payload):
    monkeypatch.setattr(
        "patchcycle.health.subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=payload),
    )
    count, _ = _default_failed_units()
    assert count == -1


def test_service_check_uses_configured_timeout(monkeypatch):
    import subprocess

    from patchcycle.config import HealthCheckConfig
    from patchcycle.health import HealthCheckRunner

    observed = []

    def run(argv, **kwargs):
        observed.append(kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 0, "active", "")

    monkeypatch.setattr("patchcycle.health.subprocess.run", run)
    results = HealthCheckRunner(failed_units=lambda: (0, "")).run(
        (HealthCheckConfig("service", name="nginx", timeout_s=7),)
    )
    assert results[0].ok
    assert observed == [7]
