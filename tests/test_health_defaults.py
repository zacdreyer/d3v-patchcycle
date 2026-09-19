"""Tests for the default (real) health-check executors, cross-platform parts."""

from __future__ import annotations

import http.server
import socket
import threading

from patchcycle.health import (
    _default_http_get,
    _default_run_command,
    _default_tcp_connect,
)


class TestTcpConnect:
    def test_open_port(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            ok, detail = _default_tcp_connect("127.0.0.1", port, 2.0)
            assert ok is True
        finally:
            listener.close()

    def test_closed_port(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        ok, detail = _default_tcp_connect("127.0.0.1", port, 1.0)
        assert ok is False
        assert detail


class TestHttpGet:
    def test_status_200(self):
        handler = http.server.BaseHTTPRequestHandler

        class H(handler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            status, _ = _default_http_get(f"http://127.0.0.1:{server.server_port}/health", 2.0)
            assert status == 200
        finally:
            server.shutdown()

    def test_wrong_status_and_unreachable(self):
        server = http.server.HTTPServer(
            ("127.0.0.1", 0),
            type(
                "H",
                (http.server.BaseHTTPRequestHandler,),
                {
                    "do_GET": lambda s: (s.send_response(500), s.end_headers()),
                    "log_message": lambda *a: None,
                },
            ),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            status, detail = _default_http_get(f"http://127.0.0.1:{server.server_port}/", 2.0)
            assert status == 500
        finally:
            server.shutdown()
        status, detail = _default_http_get("http://127.0.0.1:1/unreachable", 1.0)
        assert status == 0
        assert detail


class TestRunCommand:
    def test_exit_code_and_output(self, trusted_python):
        code, _ = _default_run_command((trusted_python, "-c", "pass"), 5.0)
        assert code == 0
        code, detail = _default_run_command(
            (
                trusted_python,
                "-c",
                "import sys; sys.stderr.write('bad'); sys.exit(2)",
            ),
            5.0,
        )
        assert code == 2
        assert "bad" in detail
