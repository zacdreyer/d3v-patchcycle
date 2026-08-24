#!/usr/bin/env python3
"""Host-side webhook sink for the L4 reboot harness.

Listens on 127.0.0.1:18080; the guest (QEMU user-net) reaches it at
10.0.2.2:18080. Writes each POSTed report body to the path in argv[1] and
exits after the first successful report or after a timeout.
"""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

TIMEOUT_S = 900


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: webhook_sink.py <report-file>", file=sys.stderr)
        return 2
    report_file = sys.argv[1]
    got = threading.Event()

    class Sink(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            with open(report_file, "w", encoding="utf-8") as fh:
                fh.write(body)
            self.send_response(200)
            self.end_headers()
            got.set()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 18080), Sink)
    timer = threading.Timer(TIMEOUT_S, server.shutdown)
    timer.daemon = True
    timer.start()
    print(f"webhook sink listening on 127.0.0.1:18080 -> {report_file}", flush=True)
    try:
        while not got.is_set():
            server.handle_request()
    finally:
        server.server_close()
    return 0 if got.is_set() else 1


if __name__ == "__main__":
    raise SystemExit(main())
