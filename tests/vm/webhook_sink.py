#!/usr/bin/env python3
"""Guest-local webhook sink for the L4 reboot harness.

Listens on the disposable guest's 127.0.0.1:18080 interface.
Writes each POSTed report body to the path in argv[1] and
exits after the first successful report or after a timeout.
"""

from __future__ import annotations

import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

TIMEOUT_S = 900


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: webhook_sink.py <report-file>", file=sys.stderr)
        return 2
    report_file = sys.argv[1]
    got = False

    class Sink(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal got
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            with open(report_file, "w", encoding="utf-8") as fh:
                fh.write(body)
            self.send_response(200)
            self.end_headers()
            got = True

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 18080), Sink)
    server.timeout = 1
    deadline = time.monotonic() + TIMEOUT_S
    print(f"webhook sink listening on 127.0.0.1:18080 -> {report_file}", flush=True)
    try:
        while not got and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
