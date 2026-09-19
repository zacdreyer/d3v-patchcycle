"""Transport and health fail-closed audit regressions."""

import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from patchcycle.config import EmailConfig
from patchcycle.health import HealthCheckRunner
from patchcycle.notify.smtp import SmtpNotifier
from test_notify import BODY, REPORT


def test_smtp_starttls_verifies_certificate_and_hostname(monkeypatch):
    contexts = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def starttls(self, *, context=None):
            contexts.append(context)

        def send_message(self, msg):
            return {}

        def quit(self):
            pass

    monkeypatch.setattr("patchcycle.notify.smtp.smtplib.SMTP", SMTP)
    assert SmtpNotifier(EmailConfig(smtp_starttls=True)).deliver(REPORT, BODY) == "sent"
    assert contexts[0] is not None
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname


def test_smtp_server_error_never_echoed_in_status(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("canary-password")

    monkeypatch.setattr("patchcycle.notify.smtp.smtplib.SMTP", fail)
    status = SmtpNotifier(EmailConfig()).deliver(REPORT, BODY)
    assert status.startswith("failed:")
    assert "canary-password" not in status


def test_failed_units_exception_becomes_failed_health_result():
    def fail():
        raise OSError("systemd unavailable")

    results = HealthCheckRunner(failed_units=fail).run(())
    assert len(results) == 1
    assert not results[0].ok


def test_webhook_does_not_follow_redirect_with_credentials():
    from patchcycle.config import WebhookConfig
    from patchcycle.notify.webhook import WebhookNotifier

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(302)
            self.send_header("Location", "/capture")
            self.end_headers()

        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = WebhookConfig(
            url=f"http://127.0.0.1:{server.server_port}/redirect",
            headers=(("Authorization", "canary"),),
        )
        assert WebhookNotifier(config).deliver(REPORT, BODY).startswith("failed:")
        assert seen == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
