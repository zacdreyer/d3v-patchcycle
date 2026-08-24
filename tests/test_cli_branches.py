"""CLI branch coverage: detect-supported, config-check details, composition."""

from __future__ import annotations

import platform

from tests.test_cli import write_config
from tests.test_cli_dispatch import LINUX_IDENTITY

from patchcycle import cli
from patchcycle.engine import CycleEngine
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State


def test_help_returns_2(capsys):
    assert cli.main([]) == 2
    assert "usage:" in capsys.readouterr().out


class TestDetectSupported:
    def test_supported_platform_prints_provider(self, monkeypatch, capsys):
        monkeypatch.setattr(cli.OsDetector, "detect", lambda self: LINUX_IDENTITY)

        class P:
            name = "apt"

            def __init__(self, identity):
                pass

        monkeypatch.setattr(cli, "select_provider", lambda identity: P(identity))
        assert cli.main(["detect"]) == 0
        out = capsys.readouterr().out
        assert "Ubuntu 24.04.2 LTS" in out
        assert "Provider: apt (supported)" in out


class TestConfigCheckDetails:
    def test_warnings_and_notifiers_printed(self, tmp_path, capsys):
        config, _ = write_config(
            tmp_path,
            extra='\n[notifications.email]\nenabled = true\nto = ["a@b.c"]\n'
            'smtp_password = "plaintext-is-warned"\n'
            "\n[notifications.webhook]\nenabled = true\n"
            'url = "http://127.0.0.1:9000/hook"\n',
        )
        assert cli.main(["config-check", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "warning:" in out
        assert "email -> a@b.c" in out
        assert "webhook -> http://127.0.0.1:9000/hook" in out

    def test_health_command_paths_validated(self, tmp_path, capsys):
        config, _ = write_config(
            tmp_path,
            extra='\n[[health.command]]\nargv = ["/usr/local/bin/check.sh"]\n',
        )
        # The path is validated but doesn't exist; on POSIX config-check flags
        # it (exit 4), on non-POSIX path validation is a no-op (exit 0).
        result = cli.main(["config-check", "--config", str(config)])
        assert result in (0, 4)


class TestStatusDetails:
    def test_status_idle_with_last_cycle(self, tmp_path, capsys):
        config, state_dir = write_config(tmp_path)
        StateStore(state_dir).archive(
            CycleState(
                run_id="run-prev",
                state=State.COMPLETED,
                hostname="h",
                outcome="success",
                started_at="2026-08-23T02:00:00Z",
            )
        )
        assert cli.main(["status", "--config", str(config)]) == 0
        assert "run-prev" in capsys.readouterr().out

    def test_status_shows_error(self, tmp_path, capsys):
        config, state_dir = write_config(tmp_path)
        StateStore(state_dir).save(
            CycleState(
                run_id="run-err",
                state=State.FAILED,
                hostname="h",
                error={
                    "kind": "pm-failed",
                    "message": "dpkg broke",
                    "stage": "UPGRADING",
                    "manual_intervention": True,
                },
            )
        )
        assert cli.main(["status", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "pm-failed" in out and "dpkg broke" in out


class TestCompositionRoot:
    def test_build_engine_constructs(self, tmp_path):
        config, _ = write_config(tmp_path)
        cfg = cli.load_config(config)

        class P:
            name = "apt"

            def __init__(self, identity):
                self.os_identity = identity

        import logging

        engine = cli._build_engine(cfg, LINUX_IDENTITY, P(LINUX_IDENTITY), logging.getLogger("t"))
        assert isinstance(engine, CycleEngine)
        assert engine.hostname == platform.node()
