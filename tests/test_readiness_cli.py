"""CLI boundary regressions."""

from pathlib import Path

import pytest

from patchcycle.cli import build_parser, main
from patchcycle.errors import PreflightError, RebootError
from patchcycle.state_store import CycleState
from patchcycle.states import State
from test_engine import ScriptedProvider, make_engine


def test_config_before_subcommand_is_preserved():
    assert build_parser().parse_args(["--config", "/custom/config", "status"]).config == Path(
        "/custom/config"
    )


def test_status_invalid_custom_config_does_not_report_default_state(tmp_path, capsys):
    config = tmp_path / "broken.toml"
    config.write_text("invalid = [")
    assert main(["status", "--config", str(config)]) == 4
    output = capsys.readouterr()
    assert "IDLE" not in output.out
    assert "config" in output.err.lower()


def test_cli_handles_domain_failure_without_traceback(monkeypatch, capsys):
    def fail(_):
        raise PreflightError("unhealthy")

    monkeypatch.setattr("patchcycle.cli._cmd_cycle", fail)
    assert main(["run", "--dry-run"]) == PreflightError.exit_code
    assert "unhealthy" in capsys.readouterr().err


@pytest.mark.parametrize("before,current", [(None, "boot"), ("boot", "")])
def test_missing_boot_identity_cannot_prove_reboot(tmp_path, before, current):
    engine, _, _ = make_engine(tmp_path, ScriptedProvider())
    engine.reboot.boot_id_reader = lambda: current
    with pytest.raises(RebootError):
        engine.reboot.verify_rebooted(
            CycleState("run", State.POST_REBOOT, "host", boot_id_before=before)
        )


def test_interactive_resume_requires_force(monkeypatch, capsys):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert main(["resume"]) == 2
    assert "--force" in capsys.readouterr().err


def test_unknown_login_probe_blocks_reboot(monkeypatch):
    from types import SimpleNamespace

    from patchcycle import cli

    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="")
    )
    assert cli._users_logged_in() is True


def test_idle_status_shows_last_delivery_failure(tmp_path, capsys):
    from patchcycle.state_store import StateStore
    from test_cli import write_config

    config, state = write_config(tmp_path)
    StateStore(state).archive(
        CycleState(
            "finished",
            State.COMPLETED,
            "host",
            outcome="success",
            notification_status={"smtp": "failed:SMTPException"},
        )
    )
    assert main(["status", "--config", str(config)]) == 0
    assert "failed:SMTPException" in capsys.readouterr().out


def test_config_check_does_not_echo_secret_webhook_path(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        '[notifications.webhook]\nenabled=true\nurl="https://example.invalid/canary-token"\n'
    )
    assert main(["config-check", "--config", str(config)]) == 0
    assert "canary-token" not in capsys.readouterr().out
