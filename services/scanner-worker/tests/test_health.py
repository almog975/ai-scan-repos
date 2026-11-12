import json
import socket
import urllib.request

from scanner.health import start_health_server


def test_probe_server_serves_health_ready_metrics():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = start_health_server(port)
    try:
        get = lambda p: urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=2)  # noqa: E731
        assert json.loads(get("/healthz").read()) == {"status": "ok"}
        assert json.loads(get("/readyz").read()) == {"status": "ready"}
        body = get("/metrics").read().decode()
        assert "scan_attempts_total" in body and "scan_stage" in body
    finally:
        srv.shutdown()
