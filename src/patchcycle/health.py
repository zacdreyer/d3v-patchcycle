"""Health checks (product spec FR-13; configuration.md §health).

Read-only by contract. Each check returns a HealthResult; criticality rollup
is the engine's job. Executors are injectable for tests.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable

from patchcycle.config import HealthCheckConfig
from patchcycle.models import HealthResult

_CHECK_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}


class HealthCheckRunner:
    def __init__(
        self,
        *,
        service_active: Callable[[str], tuple[bool, str]] | None = None,
        http_get: Callable[[str, float], tuple[int, str]] | None = None,
        tcp_connect: Callable[[str, int, float], tuple[bool, str]] | None = None,
        run_command: Callable[[tuple[str, ...], float], tuple[int, str]] | None = None,
        failed_units: Callable[[], tuple[int, str]] | None = None,
    ) -> None:
        self._service_active = service_active or _default_service_active
        self._http_get = http_get or _default_http_get
        self._tcp_connect = tcp_connect or _default_tcp_connect
        self._run_command = run_command or _default_run_command
        self._failed_units = failed_units or _default_failed_units

    def run(self, checks: tuple[HealthCheckConfig, ...]) -> list[HealthResult]:
        results = [self._run_one(c) for c in checks]
        results.append(self._check_failed_units())
        return results

    def _run_one(self, check: HealthCheckConfig) -> HealthResult:
        start = time.monotonic()
        ok, detail = False, ""
        try:
            if check.kind == "service":
                ok, detail = self._service_active(check.name)
            elif check.kind == "http":
                status, detail = self._http_get(check.url, check.timeout_s)
                ok = status == check.expected_status
                detail = f"status={status} expected={check.expected_status} {detail}".strip()
            elif check.kind == "tcp":
                ok, detail = self._tcp_connect(check.host, check.port, check.timeout_s)
            elif check.kind == "command":
                code, detail = self._run_command(check.argv, check.timeout_s)
                ok = code == 0
                detail = f"exit={code} {detail}".strip()
        except Exception as exc:  # checks must never crash the cycle
            ok, detail = False, f"check error: {exc}"
        return HealthResult(
            kind=check.kind,
            name=check.name,
            ok=ok,
            critical=check.critical,
            detail=detail,
            duration_s=time.monotonic() - start,
        )

    def _check_failed_units(self) -> HealthResult:
        count, detail = self._failed_units()
        return HealthResult(
            kind="failed-units",
            name="systemd-failed-units",
            ok=count == 0,
            critical=False,
            detail=detail or f"{count} failed units",
        )


def _default_service_active(name: str) -> tuple[bool, str]:
    unit = name if "." in name else f"{name}.service"
    # S603: argv list, no shell, scrubbed env, name validated by config schema.
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/systemctl", "is-active", "--quiet", unit],
        shell=False,
        env=_CHECK_ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0, proc.stdout.strip()


def _default_http_get(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc)
    except Exception as exc:
        return 0, str(exc)


def _default_tcp_connect(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def _default_run_command(argv: tuple[str, ...], timeout: float) -> tuple[int, str]:
    # S603: argv list, no shell, scrubbed env, absolute path enforced by config.
    proc = subprocess.run(  # noqa: S603
        list(argv),
        shell=False,
        env=_CHECK_ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, (proc.stderr or proc.stdout or "")[-500:]


def _default_failed_units() -> tuple[int, str]:  # pragma: no cover - Linux/systemd-only (L3)
    # S603: fixed argv, no shell, scrubbed env.
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/systemctl", "list-units", "--failed", "--no-legend", "--plain", "--output=json"],
        shell=False,
        env=_CHECK_ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0, f"failed-units probe unavailable: {proc.stderr.strip()}"
    try:
        units = json.loads(proc.stdout or "[]")
    except ValueError:
        return 0, "failed-units probe returned unparsable output"
    names = [u.get("unit", "?") for u in units] if isinstance(units, list) else []
    return len(names), ", ".join(names)
