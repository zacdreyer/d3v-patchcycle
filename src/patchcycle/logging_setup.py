"""Structured logging with run correlation and secret redaction.

Spec: architecture §9, threat-model T5/T13. Under systemd, stdout lands in
journald automatically; the optional file handler writes JSONL records that
always carry run_id and state. Newlines in values are escaped by JSON
encoding, so one event is always exactly one line (log-injection safe).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchcycle.config import Config

LOGGER_NAME = "patchcycle"


class RedactionFilter(logging.Filter):
    """Redacts configured secret values from every record (T5)."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        for secret in self._secrets:
            msg = msg.replace(secret, "***REDACTED***")
        record.msg = msg
        record.args = ()
        return True


class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line; cycle fields always present."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": getattr(record, "run_id", None),
            "state": getattr(record, "state", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


class _CycleAdapter(logging.LoggerAdapter[logging.Logger]):
    """Injects run_id/state into every record."""

    def process(  # noqa: D102
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        base = self.extra or {}
        extra.setdefault("run_id", base.get("run_id"))
        extra.setdefault("state", base.get("state"))
        return msg, kwargs


def cycle_logger(run_id: str, state: str) -> logging.LoggerAdapter[logging.Logger]:
    """Return a logger that stamps every record with run_id and state."""
    return _CycleAdapter(logging.getLogger(LOGGER_NAME), {"run_id": run_id, "state": state})


def configure_logging(
    *,
    level: str = "info",
    log_file: Path | None = None,
    secrets: list[str] | None = None,
) -> logging.Logger:
    """Configure the patchcycle logger. Idempotent.

    Console output is plain and goes to stderr (journald captures it when
    running under systemd); the optional file handler is JSONL.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    redact = RedactionFilter(secrets or [])

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.addFilter(redact)
    logger.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonLinesFormatter())
        file_handler.addFilter(redact)
        logger.addHandler(file_handler)

    return logger


def collect_config_secrets(cfg: Config) -> list[str]:
    """Gather secret values the redaction filter must scrub from logs."""
    import os

    secrets: list[str] = []
    email = cfg.notifications.email
    if email.smtp_password:
        secrets.append(email.smtp_password)
    if email.smtp_password_env:
        value = os.environ.get(email.smtp_password_env)
        if value:
            secrets.append(value)
    for _name, value in cfg.notifications.webhook.headers:
        if value.startswith("env:"):
            resolved = os.environ.get(value[4:])
            if resolved:
                secrets.append(resolved)
    return secrets
