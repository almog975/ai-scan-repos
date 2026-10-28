"""Threaded mock AI HTTP server speaking the three wire formats AIClient supports:
OpenAI-style POST {base}/chat/completions, Anthropic POST {base}/messages, Gemini POST {base}/models/{m}:generateContent.
`plan` is a list of HTTP status codes consumed one per request (then 200 forever)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockAI:
    def __init__(self, plan=None):
        self.plan, self.requests = list(plan or []), []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
                mock.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": body})
                code = mock.plan.pop(0) if mock.plan else 200
                n = len(mock.requests)
                text = f"mock answer #{n}"
                if code != 200:
                    payload = {"error": {"message": f"mock error {code}"}}
                elif self.path.endswith("/chat/completions"):
                    payload = {"choices": [{"message": {"role": "assistant", "content": text}}]}
                elif self.path.endswith("/messages"):
                    payload = {"content": [{"type": "text", "text": text}]}
                elif ":generateContent" in self.path:
                    payload = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
                else:
                    code, payload = 404, {"error": "unknown path"}
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
