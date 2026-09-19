"""Regressions for the September security and recovery review."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from patchcycle.config import EmailConfig, parse_config
from patchcycle.errors import StateError
from patchcycle.health import _default_http_get
from patchcycle.installer import UNIT_SERVICE
from patchcycle.logging_setup import redact_text
from patchcycle.models import OsIdentity
from patchcycle.notify.smtp import SmtpNotifier
from patchcycle.providers.apt import AptProvider
from patchcycle.providers.dnf import DnfProvider
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State
from patchcycle.subproc import CommandResult, run_argv
from test_engine import ScriptedProvider, make_engine
from test_installer import TestInstaller as InstallerFixture
from test_notify import BODY, REPORT


@pytest.mark.parametrize("state", [State.CHECKING_REBOOT, State.REBOOT_PENDING])
def test_same_boot_recovery_after_upgrade_does_not_restart_upgrade(tmp_path, state):
    provider = ScriptedProvider()
    engine, store, env = make_engine(tmp_path, provider)
    store.save(
        CycleState("restart", state, "host", updates_applied=True, boot_id_before=env.boot_id)
    )
    assert engine.run() == 0
    assert "apply" not in provider.calls
    assert store.history()[0].error is None


@pytest.mark.parametrize("provider_type", [AptProvider, DnfProvider])
@pytest.mark.parametrize("strategy", ["security", "full"])
def test_fresh_provider_verifies_configured_strategy(
    fake_bin, fake_dnf_bin, provider_type, strategy, monkeypatch
):
    provider = provider_type(
        OsIdentity("linux", "fixture"),
        search_paths=(str(fake_bin if provider_type is AptProvider else fake_dnf_bin),),
        runner=lambda *a, **kw: CommandResult(0, "", ""),
    )
    provider.configure(parse_config({"updates": {"strategy": strategy}}))
    seen = []
    monkeypatch.setattr(provider, "list_updates", lambda value: seen.append(value) or [])
    assert provider.verify().consistent
    assert seen == [strategy]


@pytest.mark.parametrize(
    "field,value",
    [
        ("packages_pending", None),
        ("packages_updated", None),
        ("reboot_attempts", None),
        ("outcome", "unrecognized"),
        (
            "health_results",
            [{"kind": "http", "name": "x", "ok": True, "critical": True, "duration_s": 10**400}],
        ),
    ],
)
def test_malformed_state_is_quarantined_and_history_skipped(tmp_path, field, value):
    store = StateStore(tmp_path)
    data = CycleState("invalid", State.VERIFYING, "host").to_dict()
    data[field] = value
    store.state_file.write_text(json.dumps(data))
    with pytest.raises(StateError, match="quarantined"):
        store.load()
    store.history_dir.mkdir()
    (store.history_dir / "invalid.json").write_text(json.dumps(data))
    assert store.history() == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and symlinks")
@pytest.mark.parametrize("unsafe", ["symlink", "writable"])
def test_matching_installed_unit_still_requires_safe_file(tmp_path, unsafe):
    inst, _ = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    unit = inst.unit_dir / UNIT_SERVICE
    if unsafe == "symlink":
        target = tmp_path / "external.service"
        unit.rename(target)
        unit.symlink_to(target)
    else:
        unit.chmod(0o666)
    assert not inst.install().ok


def test_overlapping_secrets_do_not_leave_suffixes():
    assert redact_text("abc-private-suffix", ["abc", "abc-private-suffix"]) == "***REDACTED***"


def test_non_utf8_command_output_keeps_successful_exit_status():
    result = run_argv(
        [sys.executable, "-c", "import os; os.write(1, b'\\xff'); os.write(2, b'\\xfe')"], timeout=5
    )
    assert result.exit_code == 0
    assert result.stdout == result.stderr == "\ufffd"


def test_smtp_partial_recipient_failure_is_not_sent(monkeypatch):
    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def send_message(self, msg):
            return {"private@example.invalid": (550, b"private rejection detail")}

        def quit(self):
            pass

    monkeypatch.setattr("patchcycle.notify.smtp.smtplib.SMTP", SMTP)
    assert SmtpNotifier(EmailConfig()).deliver(REPORT, BODY) == "failed:recipients-refused"


@pytest.mark.parametrize("proxy", ["", "http://127.0.0.1:1"])
def test_http_health_uses_original_status_and_ignores_environment_proxy(monkeypatch, proxy):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302 if self.path == "/health" else 200)
            self.send_header("Location", "/login")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("http_proxy", proxy)
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        status, _ = _default_http_get(f"http://127.0.0.1:{server.server_port}/health", 2)
        assert status == 302
        assert seen == ["/health"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("blocker", ["window", "users", "resume-service"])
def test_reboot_command_retry_rechecks_safety_after_wait(tmp_path, blocker):
    from datetime import datetime

    from patchcycle.models import RebootStatus

    engine, store, _ = make_engine(
        tmp_path,
        ScriptedProvider(reboot=RebootStatus(True)),
        config_text='[maintenance]\nwindow_start="02:00"\nwindow_end="04:00"\n',
    )
    attempts = []

    def fail_reboot():
        attempts.append("reboot")
        raise OSError("transient failure")

    def wait(_):
        if blocker == "window":
            engine.reboot.now_local = lambda: datetime(2026, 8, 23, 5)
        elif blocker == "users":
            engine.reboot.users_logged_in = lambda: True
        else:
            engine.reboot.resume_path_ok = lambda: False

    engine.reboot.rebooter = fail_reboot
    engine.reboot.sleeper = wait
    assert engine.run() != 0
    assert attempts == ["reboot"]
    assert store.history()[0].reboot_attempts == 1
