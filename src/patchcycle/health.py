"""Health checks (product spec FR-13; configuration.md §health).

Read-only by contract. Each check returns a HealthResult; criticality rollup
is the engine's job. Executors are injectable for tests.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
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
        self._service_active = service_active
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
                ok, detail = (
                    self._service_active(check.name)
                    if self._service_active
                    else _default_service_active(check.name, check.timeout_s)
                )
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
        try:
            count, detail = self._failed_units()
        except Exception as exc:
            count, detail = -1, f"failed-units probe failed: {type(exc).__name__}"
        return HealthResult(
            kind="failed-units",
            name="systemd-failed-units",
            ok=count == 0,
            critical=False,
            detail=detail or f"{count} failed units",
        )


def _default_service_active(name: str, timeout: float = 10) -> tuple[bool, str]:
    unit = name if "." in name else f"{name}.service"
    # S603: argv list, no shell, scrubbed env, name validated by config schema.
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/systemctl", "is-active", "--quiet", unit],
        shell=False,
        env=_CHECK_ENV,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode == 0, proc.stdout.strip()


class _NoHealthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def _default_http_get(url: str, timeout: float) -> tuple[int, str]:
    resp = None
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoHealthRedirect())
        resp = opener.open(url, timeout=timeout)
        return resp.status, ""
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code, str(exc)
    except Exception as exc:
        return 0, str(exc)
    finally:
        if resp is not None:
            resp.close()


def _default_tcp_connect(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def _default_run_command(argv: tuple[str, ...], timeout: float) -> tuple[int, str]:
    from patchcycle.hooks import validate_hook_paths
    from patchcycle.subproc import run_argv

    problems = validate_hook_paths((argv,))
    if problems:
        return -1, "; ".join(problems)
    proc = run_argv(
        list(argv),
        extra_env=_CHECK_ENV,
        timeout=timeout,
    )
    return proc.exit_code, (proc.stderr or proc.stdout or "")[-500:]


def _default_failed_units() -> tuple[int, str]:  # pragma: no cover - Linux/systemd-only (L3)
    # S603: fixed argv, no shell, scrubbed env.
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/systemctl", "list-units", "--failed", "--no-legend", "--plain", "--output=json"],
        shell=False,
        env=_CHECK_ENV,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        return -1, f"failed-units probe unavailable: {proc.stderr.strip()}"
    try:
        units = json.loads(proc.stdout or "[]")
    except ValueError:
        return -1, "failed-units probe returned unparsable output"
    if not isinstance(units, list) or not all(
        isinstance(u, dict) and isinstance(u.get("unit"), str) for u in units
    ):
        return -1, "failed-units probe returned invalid unit records"
    names = [u["unit"] for u in units]
    return len(names), ", ".join(names)
