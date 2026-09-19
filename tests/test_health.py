"""Tests for health checks (product spec FR-13; configuration.md §health)."""

from __future__ import annotations

from patchcycle.config import HealthCheckConfig
from patchcycle.health import HealthCheckRunner

CHECKS = (
    HealthCheckConfig("service", critical=True, name="nginx"),
    HealthCheckConfig(
        "http", critical=True, name="h", url="http://127.0.0.1/health", expected_status=200
    ),
    HealthCheckConfig("tcp", critical=False, name="db", host="127.0.0.1", port=5432),
    HealthCheckConfig("command", critical=True, name="check.sh", argv=("/usr/local/bin/check.sh",)),
)


class TestCheckKinds:
    def test_all_kinds_via_fakes(self):
        runner = HealthCheckRunner(
            service_active=lambda name: (name == "nginx", "active"),
            http_get=lambda url, timeout: (200, ""),
            tcp_connect=lambda host, port, timeout: (True, ""),
            run_command=lambda argv, timeout: (0, ""),
            failed_units=lambda: (0, ""),
        )
        results = runner.run(CHECKS)
        assert len(results) == 5  # 4 configured + implicit failed-units
        assert all(r.ok for r in results)
        assert {r.kind for r in results} == {"service", "http", "tcp", "command", "failed-units"}

    def test_http_wrong_status_fails(self):
        runner = HealthCheckRunner(
            http_get=lambda url, timeout: (503, "backend down"),
            failed_units=lambda: (0, ""),
        )
        results = runner.run(CHECKS[1:2])
        assert results[0].ok is False
        assert "503" in results[0].detail

    def test_failed_units_detected(self):
        runner = HealthCheckRunner(
            failed_units=lambda: (2, "nginx.service, cron.service"),
        )
        results = runner.run(())
        assert results[0].kind == "failed-units"
        assert results[0].ok is False
        assert results[0].critical is False  # reported, not cycle-fatal
        assert "nginx.service" in results[0].detail

    def test_check_exception_becomes_failure_not_crash(self):
        def explode(name):
            raise RuntimeError("systemctl exploded")

        runner = HealthCheckRunner(service_active=explode, failed_units=lambda: (0, ""))
        results = runner.run(CHECKS[:1])
        assert results[0].ok is False
        assert "exploded" in results[0].detail

    def test_real_command_check(self, trusted_python):
        runner = HealthCheckRunner(failed_units=lambda: (0, ""))
        ok_check = HealthCheckConfig(
            "command",
            critical=True,
            name="true",
            argv=(trusted_python, "-c", "pass"),
        )
        bad_check = HealthCheckConfig(
            "command",
            critical=True,
            name="false",
            argv=(trusted_python, "-c", "import sys; sys.exit(1)"),
        )
        results = runner.run((ok_check, bad_check))
        assert results[0].ok is True
        assert results[1].ok is False
        assert "exit=1" in results[1].detail
