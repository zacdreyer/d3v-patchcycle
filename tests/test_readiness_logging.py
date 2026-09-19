"""Secrets in exceptions and privileged logging paths."""

import logging
import os
import sys

import pytest

from patchcycle.logging_setup import RedactionFilter, configure_logging


def test_all_config_secret_sources_are_redacted(monkeypatch):
    from patchcycle.config import parse_config
    from patchcycle.logging_setup import collect_config_secrets

    monkeypatch.setenv("MAIL_SECRET", "mail-env-canary")
    monkeypatch.setenv("HOOK_SECRET", "hook-env-canary")
    cfg = parse_config(
        {
            "notifications": {
                "email": {
                    "smtp_password": "literal-mail-canary",
                    "smtp_password_env": "MAIL_SECRET",
                },
                "webhook": {
                    "url": "https://example.invalid/private-token",
                    "headers": {
                        "Authorization": "env:HOOK_SECRET",
                        "X-Key": "literal-hook-canary",
                        "X-Missing": "env:ABSENT_CANARY",
                    },
                },
            }
        }
    )
    secrets = collect_config_secrets(cfg)
    assert set(secrets) == {
        "mail-env-canary",
        "literal-mail-canary",
        "hook-env-canary",
        "literal-hook-canary",
        "https://example.invalid/private-token",
    }
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, " ".join(secrets), (), None)
    RedactionFilter(secrets).filter(record)
    assert all(secret not in record.getMessage() for secret in secrets)


def test_exception_traceback_is_redacted():
    try:
        raise RuntimeError("canary-secret")
    except RuntimeError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    RedactionFilter(["canary-secret"]).filter(record)
    rendered = logging.Formatter().format(record)
    assert "canary-secret" not in rendered
    assert "RuntimeError" in rendered


@pytest.mark.skipif(os.name != "posix", reason="POSIX file protections")
def test_log_symlink_is_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("untouched")
    path = tmp_path / "log"
    path.symlink_to(target)
    with pytest.raises(OSError):
        configure_logging(log_file=path)
    assert target.read_text() == "untouched"


def test_engine_logs_carry_cycle_correlation(tmp_path, caplog):
    from test_engine import ScriptedProvider, make_engine

    engine, store, _ = make_engine(tmp_path, ScriptedProvider())
    caplog.set_level(logging.INFO, logger="patchcycle-test")
    assert engine.run() == 0
    run_id = store.history()[0].run_id
    records = [r for r in caplog.records if r.name == "patchcycle-test"]
    assert records
    assert all(getattr(r, "run_id", None) == run_id for r in records)
    assert all(getattr(r, "state", None) for r in records)
