"""Generic webhook notifier (ADR-0007).

Posts a JSON payload (structured fields + plain-text body) via stdlib urllib.
Header values prefixed ``env:`` resolve from the environment (secrets never
in config); ``Authorization`` values gain a ``Bearer `` prefix unless they
already carry an auth scheme.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict

from patchcycle.config import WebhookConfig
from patchcycle.models import ReportData
from patchcycle.notify.base import Notifier


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def resolve_headers(headers: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    """Resolve env:-indirected header values; missing vars omit the header."""
    resolved: list[tuple[str, str]] = []
    for name, value in headers:
        if value.startswith("env:"):
            env_value = os.environ.get(value[4:], "")
            if not env_value:
                continue
            value = env_value
        if name.lower() == "authorization" and " " not in value:
            value = f"Bearer {value}"
        resolved.append((name, value))
    return resolved


class WebhookNotifier(Notifier):
    name = "webhook"

    def __init__(self, config: WebhookConfig) -> None:
        self.config = config

    def deliver(self, report: ReportData, body: str) -> str:
        data = asdict(report)
        data["outcome"] = report.outcome.value
        data["host"] = report.hostname
        data["text"] = body
        payload = json.dumps(data, default=str).encode()
        # S310: URL scheme is validated by the config schema (https required
        # off-loopback); secrets travel in headers, never in the URL.
        request = urllib.request.Request(  # noqa: S310
            self.config.url,
            data=payload,
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        for name, value in resolve_headers(self.config.headers):
            request.add_header(name, value)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=self.config.timeout_s) as resp:
                if 200 <= resp.status < 300:
                    return "sent"
                return f"failed:http-{resp.status}"
        except urllib.error.HTTPError as exc:
            return f"failed:http-{exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            # URLError reasons can embed URLs (never secrets — secrets ride in
            # headers only, which are never stringified here).
            return f"failed:{type(exc).__name__}"
