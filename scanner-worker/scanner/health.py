"""Tiny probe server for the scanner Job pod: /healthz, /readyz, /metrics (prometheus_client)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

READY = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._send(200, b'{"status":"ok"}', "application/json")
        elif self.path == "/readyz":
            ok = READY.is_set()
            self._send(200 if ok else 503, b'{"status":"ready"}' if ok else b'{"status":"starting"}', "application/json")
        elif self.path == "/metrics":
            self._send(200, generate_latest(), CONTENT_TYPE_LATEST)
        else:
            self._send(404, b"", "text/plain")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence access log
        pass


def start_health_server(port: int = 8002) -> ThreadingHTTPServer | None:
    if port <= 0:
        return None
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except OSError:
        return None  # port busy (e.g. local tests) - probes are best effort
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    READY.set()
    return srv
