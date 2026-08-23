"""Tests for structured logging and secret redaction (threat-model T5/T13)."""

from __future__ import annotations

import json
import logging

from patchcycle.logging_setup import (
    JsonLinesFormatter,
    RedactionFilter,
    configure_logging,
    cycle_logger,
)


class TestRedaction:
    def test_configured_secrets_are_redacted(self):
        filt = RedactionFilter(secrets=["hunter2", "tok_abc123"])
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "password is hunter2", (), None)
        filt.filter(record)
        assert "hunter2" not in record.getMessage()
        assert "REDACTED" in record.getMessage()

    def test_authorization_header_redacted(self):
        filt = RedactionFilter(secrets=["tok_abc123"])
        record = logging.LogRecord(
            "x",
            logging.INFO,
            __file__,
            1,
            "POST with Authorization: Bearer tok_abc123 failed",
            (),
            None,
        )
        filt.filter(record)
        assert "tok_abc123" not in record.getMessage()

    def test_no_secrets_configured_is_noop(self):
        filt = RedactionFilter(secrets=[])
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "plain", (), None)
        assert filt.filter(record) is True
        assert record.getMessage() == "plain"


class TestJsonLinesFormatter:
    def test_record_is_valid_jsonl_with_cycle_fields(self):
        fmt = JsonLinesFormatter()
        record = logging.LogRecord(
            "patchcycle", logging.WARNING, __file__, 1, "something\nhappened", (), None
        )
        record.run_id = "run-1"
        record.state = "UPGRADING"
        out = fmt.format(record)
        data = json.loads(out)  # single line, valid JSON
        assert "\n" not in out.rstrip("\n")
        assert data["run_id"] == "run-1"
        assert data["state"] == "UPGRADING"
        assert data["level"] == "WARNING"
        assert "something" in data["message"]
        # Newlines are escaped by JSON encoding: message is a single JSON string.
        assert data["message"] == "something\nhappened"

    def test_newline_injection_stays_one_record(self):
        fmt = JsonLinesFormatter()
        record = logging.LogRecord(
            "patchcycle",
            logging.INFO,
            __file__,
            1,
            'evil"\n{"run_id": "forged"}',
            (),
            None,
        )
        out = fmt.format(record)
        assert len(out.rstrip("\n").splitlines()) == 1


class TestConfiguration:
    def test_configure_logging_sets_level_and_redaction(self, tmp_path):
        log_file = tmp_path / "patchcycle.jsonl"
        configure_logging(level="debug", log_file=log_file, secrets=["s3cr3t"])
        log = cycle_logger("run-42", "PRECHECK")
        log.info("hello")
        log.debug("secret is s3cr3t")
        logging.getLogger("patchcycle").handlers[0].flush()
        lines = log_file.read_text().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["run_id"] == "run-42"
        assert first["state"] == "PRECHECK"
        second = json.loads(lines[1])
        assert "s3cr3t" not in lines[1]
        assert second["level"] == "DEBUG"
