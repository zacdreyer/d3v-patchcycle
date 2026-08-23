"""SMTP email notifier (ADR-0007; configuration.md §notifications.email).

stdlib only (smtplib). Credentials come from the environment via
``smtp_password_env`` (threat-model T5); the password value never appears in
results, logs, or state.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from patchcycle.config import EmailConfig
from patchcycle.models import ReportData
from patchcycle.notify.base import Notifier


class SmtpNotifier(Notifier):
    name = "smtp"

    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    def deliver(self, report: ReportData, body: str) -> str:
        cfg = self.config
        msg = EmailMessage()
        msg["From"] = cfg.from_addr or f"patchcycle@{report.hostname}"
        msg["To"] = ", ".join(cfg.to)
        msg["Subject"] = (
            f"D3V PatchCycle {report.outcome.value.upper().replace('_', ' ')}: {report.hostname}"
        )
        msg.set_content(body)
        smtp: smtplib.SMTP | None = None
        try:
            smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
            if cfg.smtp_starttls:
                smtp.starttls()
            password = ""
            if cfg.smtp_password_env:
                password = os.environ.get(cfg.smtp_password_env, "")
            elif cfg.smtp_password:
                password = cfg.smtp_password
            if cfg.smtp_username and password:
                smtp.login(cfg.smtp_username, password)
            smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            # Never include credentials or message content in the failure text.
            return f"failed:{type(exc).__name__}: {exc}"
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except (OSError, smtplib.SMTPException):
                    smtp.close()
        return "sent"
