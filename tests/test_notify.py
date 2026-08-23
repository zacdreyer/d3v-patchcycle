"""Tests for notifiers (ADR-0007; FR-14; threat-model T5/T9)."""

from __future__ import annotations

import base64  # noqa: F401 - reserved for auth-scheme tests
import json
import os  # noqa: F401 - reserved for env manipulation clarity
import threading
from email.parser import Parser
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from patchcycle.config import EmailConfig, WebhookConfig
from patchcycle.models import ErrorInfo, Outcome, ReportData
from patchcycle.notify.smtp import SmtpNotifier
from patchcycle.notify.webhook import WebhookNotifier

REPORT = ReportData(
    run_id="run-42",
    hostname="web01.example.com",
    os_pretty_name="Ubuntu 24.04 LTS",
    outcome=Outcome.FAILED,
    error=ErrorInfo(
        kind="pm-failed",
        message="Package manager returned a failure.",
        stage="UPGRADING",
        manual_intervention=True,
    ),
)
BODY = "D3V PatchCycle\n\nHost: web01.example.com\n\nResult: FAILED\n"


@pytest.fixture(autouse=True)
def _collect_garbage():
    """Server-based tests leave no threads/sockets behind between runs."""
    yield
    import gc

    gc.collect()


class FakeSMTPServer:
    """Minimal SMTP sink capturing envelope + message."""

    def __init__(self):
        self.messages: list[dict] = []
        self.server = None
        self.port = 0

    def __enter__(self):  # noqa: C901 - SMTP protocol fake is inherently linear
        import socketserver

        parent = self

        class Handler(socketserver.StreamRequestHandler):
            timeout = 30  # never block shutdown on a stuck client

            def handle(self):
                self._line = lambda: self.rfile.readline().decode().strip()
                mail_from = rcpt = None
                data_lines: list[str] = []
                self.wfile.write(b"220 fake-smtp ready\r\n")
                while True:
                    line = self._line()
                    if not line:
                        break
                    upper = line.upper()
                    if upper.startswith("EHLO") or upper.startswith("HELO"):
                        self.wfile.write(b"250 fake-smtp\r\n")
                    elif upper.startswith("MAIL FROM:"):
                        mail_from = line[10:].strip("<>")
                        self.wfile.write(b"250 OK\r\n")
                    elif upper.startswith("RCPT TO:"):
                        rcpt = line[8:].strip("<>")
                        self.wfile.write(b"250 OK\r\n")
                    elif upper == "DATA":
                        self.wfile.write(b"354 End with . on its own line\r\n")
                        while True:
                            dline = self._line()
                            if dline == ".":
                                break
                            data_lines.append(dline)
                        self.wfile.write(b"250 queued\r\n")
                        parent.messages.append({
                            "mail_from": mail_from,
                            "rcpt": rcpt,
                            "data": "\n".join(data_lines),
                        })
                    elif upper == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        break
                    else:
                        self.wfile.write(b"250 OK\r\n")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.server.socket.close()


class FakeWebhook:
    def __init__(self, status=200, capture=None):
        self.status = status
        self.requests: list[dict] = capture if capture is not None else []
        self.server = None
        self.url = ""

    def __enter__(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 30

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode()
                parent.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": body,
                })
                self.send_response(parent.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        class Server(HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class TestSmtpNotifier:
    def config(self, **overrides):
        base = {
            "enabled": True,
            "to": ("admin@example.com",),
            "from_addr": "patchcycle@web01.example.com",
            "smtp_host": "127.0.0.1",
            "smtp_port": 0,
        }
        base.update(overrides)
        return EmailConfig(**base)

    def test_delivers_report_email(self):
        with FakeSMTPServer() as smtp:
            notifier = SmtpNotifier(self.config(smtp_port=smtp.port))
            assert notifier.deliver(REPORT, BODY) == "sent"
        assert len(smtp.messages) == 1
        msg = smtp.messages[0]
        assert msg["rcpt"] == "admin@example.com"
        assert msg["mail_from"] == "patchcycle@web01.example.com"
        parsed = Parser().parsestr(msg["data"])
        assert "FAILED" in parsed["Subject"]
        assert "web01.example.com" in parsed["Subject"]
        assert BODY.strip() in parsed.get_payload()

    def test_subject_success_format(self):
        ok_report = ReportData(
            run_id="r", hostname="db01", os_pretty_name="Debian 13",
            outcome=Outcome.SUCCESS,
        )
        with FakeSMTPServer() as smtp:
            notifier = SmtpNotifier(self.config(smtp_port=smtp.port))
            notifier.deliver(ok_report, "body")
        parsed = Parser().parsestr(smtp.messages[0]["data"])
        assert "SUCCESS" in parsed["Subject"]
        assert "db01" in parsed["Subject"]

    def test_connection_refused_returns_failed_not_raises(self):
        notifier = SmtpNotifier(self.config(smtp_port=1))  # closed port
        result = notifier.deliver(REPORT, BODY)
        assert result.startswith("failed:")

    def test_env_password_never_in_logs_or_errors(self, monkeypatch):
        monkeypatch.setenv("PATCHCYCLE_SMTP_PASSWORD", "s3cr3t-pw")
        cfg = self.config(
            smtp_port=1,
            smtp_username="patchcycle",
            smtp_password_env="PATCHCYCLE_SMTP_PASSWORD",  # noqa: S106 - env var NAME
        )
        notifier = SmtpNotifier(cfg)
        result = notifier.deliver(REPORT, BODY)
        assert "s3cr3t-pw" not in result


class TestWebhookNotifier:
    def config(self, url, **overrides):
        base = {"enabled": True, "url": url, "timeout_s": 5}
        base.update(overrides)
        return WebhookConfig(**base)

    def test_posts_json_payload(self):
        with FakeWebhook() as hook:
            notifier = WebhookNotifier(self.config(hook.url))
            assert notifier.deliver(REPORT, BODY) == "sent"
        assert len(hook.requests) == 1
        req = hook.requests[0]
        assert req["content_type"] == "application/json"
        payload = json.loads(req["body"])
        assert payload["run_id"] == "run-42"
        assert payload["outcome"] == "failed"
        assert payload["host"] == "web01.example.com"
        assert payload["text"] == BODY
        assert payload["error"]["kind"] == "pm-failed"

    def test_env_header_token_resolved(self, monkeypatch):
        monkeypatch.setenv("PATCHCYCLE_WH_TOKEN", "tok_secret_123")
        with FakeWebhook() as hook:
            cfg = self.config(hook.url, headers=(("Authorization", "env:PATCHCYCLE_WH_TOKEN"),))
            notifier = WebhookNotifier(cfg)
            assert notifier.deliver(REPORT, BODY) == "sent"
        assert hook.requests[0]["authorization"] == "Bearer tok_secret_123"

    def test_http_error_returns_failed(self):
        with FakeWebhook(status=500) as hook:
            notifier = WebhookNotifier(self.config(hook.url))
            assert notifier.deliver(REPORT, BODY).startswith("failed:")

    def test_unreachable_returns_failed(self):
        notifier = WebhookNotifier(self.config("http://127.0.0.1:1/hook"))
        assert notifier.deliver(REPORT, BODY).startswith("failed:")

    def test_secret_not_in_failure_message(self, monkeypatch):
        monkeypatch.setenv("PATCHCYCLE_WH_TOKEN", "tok_secret_123")
        with FakeWebhook(status=500) as hook:
            cfg = self.config(hook.url, headers=(("Authorization", "env:PATCHCYCLE_WH_TOKEN"),))
            notifier = WebhookNotifier(cfg)
            result = notifier.deliver(REPORT, BODY)
        assert "tok_secret_123" not in result


class TestAuthHeaderShape:
    def test_bearer_prefix_added(self, monkeypatch):
        """env: values become 'Bearer <token>' unless they carry a scheme."""
        monkeypatch.setenv("T", "abc")
        from patchcycle.notify.webhook import resolve_headers

        headers = dict(resolve_headers((("Authorization", "env:T"),)))
        assert headers["Authorization"] == "Bearer abc"

    def test_explicit_scheme_preserved(self, monkeypatch):
        monkeypatch.setenv("T", "Basic dXNlcjpw")
        from patchcycle.notify.webhook import resolve_headers

        headers = dict(resolve_headers((("Authorization", "env:T"),)))
        assert headers["Authorization"] == "Basic dXNlcjpw"

    def test_missing_env_var_omits_header(self, monkeypatch):
        monkeypatch.delenv("MISSING_TOKEN", raising=False)
        from patchcycle.notify.webhook import resolve_headers

        headers = dict(resolve_headers((("Authorization", "env:MISSING_TOKEN"),)))
        assert "Authorization" not in headers
