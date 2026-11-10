"""Mock gateway for Playwright e2e: serves the built SPA (services/frontend/dist) and the
contract endpoints the UI uses (services/contracts/gateway.openapi.yaml), with a test-only
control API under /__mock/ to drive review state (progress, pause, partial...).

Implements: GitHub login (session + aiscan_csrf cookies), /me, /auth/logout,
/api/reviews (list/create), /api/reviews/{id}, pause/resume/cancel/retry with the
contract's 409 rules, report.json/.md (completed, or failed/cancelled with partial),
and /api/reviews/{id}/progress as SSE (progress/state/done events).
"""
import json, os, re, secrets, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

ACTIVE = {"queued", "running", "retrying", "stuck"}
PAUSABLE = {"running"}  # API: pause only from running
RESUMABLE = {"paused", "stuck"}
CANCELLABLE = {"queued", "running", "paused", "stuck", "retrying"}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Stack:
    def __init__(self, dist):
        self.dist = dist
        self.lock = threading.Lock()
        self.sessions = {}  # sid -> csrf
        self.reviews = {}
        self.partial = {}   # id -> bool (partial results exist)
        self.calls = []     # (method, path, csrf_ok)
        self.sse_subscribers = 0
        self.providers = []        # GET /api/ai-providers (ProviderPublic[])
        self.created = []          # POST /api/reviews bodies
        # report.json provider when no AI ran (services/contracts/report.schema.json)
        self.report_provider = {"name": "none", "kind": "onprem", "model": "none"}
        self.expired = set()       # review ids whose report was removed by retention -> 410 REPORT_EXPIRED
        self.provider_test = {}    # provider id -> "ok" | "unreachable" (POST .../test)
        self.deleted_providers = []

    def new_review(self, repo_url, depth, provider_id):
        rid = str(uuid.uuid4())
        r = dict(id=rid, repo_url=repo_url, ref="HEAD", commit_sha=None, depth=depth, state="queued",
                 attempt=1, budget_no=1, files_done=0, files_total=None, findings_count=0,
                 failure_reason=None, provider=None, created_at=now(), updated_at=now(),
                 started_at=None, finished_at=None)
        self.reviews[rid] = r
        return r

    def report(self, rid):
        r = self.reviews[rid]
        partial = r["state"] != "completed"
        findings = [{"id": "f1", "severity": "high", "title": "SQL injection in app.py", "file": "app.py"}]
        if not partial:
            findings += [{"id": "f2", "severity": "medium", "title": "Missing CSRF check", "file": "views.py"},
                         {"id": "f3", "severity": "medium", "title": "Verbose error page", "file": "app.py"}]
        # summary is derived from the findings: the counts always match what is listed
        summary = {s: sum(f["severity"] == s for f in findings) for s in ("critical", "high", "medium", "low", "info")}
        summary["files_scanned"] = len(self.files(rid))
        j = {"review_id": rid, "partial": partial, "summary": summary, "provider": self.report_provider, "depth": r["depth"],
             "findings": findings}
        md = "# Security report\n\n" + ("> **Partial report** – scan did not finish.\n\n" if partial else "")
        for sev in ("critical", "high", "medium", "low", "info"):
            items = [f for f in findings if f["severity"] == sev]
            if items:
                md += f"## {sev.upper()}\n\n" + "".join(
                    f"- {f['title'].replace(f['file'], '`' + f['file'] + '`')}"
                    + (" <script>window.__xss=1</script>" if f["id"] == "f1" else "") + "\n" for f in items) + "\n"

        return j, md

    def files(self, rid):
        r = self.reviews[rid]
        done = r["state"] == "completed"
        return [{"path": "app.py", "status": "done", "findings_count": 1 if not done else 2},
                {"path": "views.py", "status": "done" if done else "scanning", "findings_count": 1 if done else 0},
                {"path": "README.md", "status": "skipped", "findings_count": 0}]

    def attempts(self, rid):
        r = self.reviews[rid]
        out = []
        for b in range(1, r["budget_no"] + 1):
            last = r["attempt"] if b == r["budget_no"] else 1
            for n in range(1, last + 1):
                final = b == r["budget_no"] and n == last
                out.append({"id": f"{rid[:8]}-{b}-{n}", "review_id": rid, "budget_no": b, "n": n, "job_name": f"scan-{rid[:8]}-{b}-{n}",
                            "started_at": r["created_at"], "ended_at": None if final and r["state"] not in ("completed", "failed", "cancelled") else now(),
                            "outcome": (None if r["state"] not in ("completed", "failed", "cancelled") else
                                        {"completed": "success", "failed": "failed", "cancelled": "cancelled"}[r["state"]]) if final else "error",
                            "error": None if final else "pod evicted"})
        return out


def make_handler(stack: Stack):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # ---------- helpers ----------
        def cookies(self):
            out = {}
            for part in (self.headers.get("Cookie") or "").split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    out[k] = v
            return out

        def send(self, code, body=b"", ctype="application/json", headers=()):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def err(self, code, ecode, msg):
            self.send(code, {"error": {"code": ecode, "message": msg}, "request_id": "req-test"})

        def session(self):
            sid = self.cookies().get("aiscan_session")
            return sid if sid in stack.sessions else None

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        # ---------- routing ----------
        def do_GET(self):
            self.route("GET")

        def do_POST(self):
            self.route("POST")

        def do_DELETE(self):
            self.route("DELETE")

        def route(self, m):
            path = urlparse(self.path).path
            if path.startswith("/__mock/"):
                return self.mock(m, path)
            if path == "/auth/github/login" and m == "GET":
                sid, csrf = secrets.token_hex(8), secrets.token_hex(8)
                stack.sessions[sid] = csrf
                return self.send(302, "", "text/plain", [("Location", "/"),
                                 ("Set-Cookie", f"aiscan_session={sid}; Path=/; HttpOnly; SameSite=Lax"),
                                 ("Set-Cookie", f"aiscan_csrf={csrf}; Path=/; SameSite=Lax")])
            if path == "/me" or path.startswith("/api/") or path == "/auth/logout":
                return self.api(m, path)
            return self.static(path)

        def static(self, path):
            f = os.path.normpath(os.path.join(stack.dist, path.lstrip("/")))
            if not f.startswith(stack.dist) or not os.path.isfile(f):
                f = os.path.join(stack.dist, "index.html")  # SPA fallback (nginx try_files)
            ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html",
                     ".svg": "image/svg+xml"}.get(os.path.splitext(f)[1], "application/octet-stream")
            with open(f, "rb") as fh:
                self.send(200, fh.read(), ctype)

        def api(self, m, path):
            sid = self.session()
            if not sid:
                return self.err(401, "UNAUTHORIZED", "login required")
            if m in ("POST", "DELETE"):
                ok = self.headers.get("X-CSRF-Token") == stack.sessions[sid]
                stack.calls.append((m, path, ok))
                if not ok:
                    return self.err(403, "FORBIDDEN", "csrf")
            else:
                stack.calls.append((m, path, True))
            if path == "/me":
                return self.send(200, {"id": "u1", "github_login": "almog975", "avatar_url": None, "role": "user"})
            if path == "/auth/logout":
                stack.sessions.pop(sid, None)
                return self.send(204, "", headers=[("Set-Cookie", "aiscan_session=; Path=/; Max-Age=0")])
            if path == "/api/ai-providers":
                return self.send(200, stack.providers)
            mp = re.fullmatch(r"/api/admin/ai-providers/([\w-]+)(/test)?", path)
            if mp:
                pid, test = mp.group(1), mp.group(2)
                prov = next((p for p in stack.providers if p["id"] == pid), None)
                if not prov:
                    return self.err(404, "NOT_FOUND", "no such provider")
                if test and m == "POST":
                    if stack.provider_test.get(pid, "ok") != "ok":
                        return self.err(502, "AI_PROVIDER_UNREACHABLE", "provider did not answer")
                    return self.send(200, {"ok": True, "latency_ms": 42, "model": prov.get("model") or "mock-model"})
                if not test and m == "DELETE":
                    active = any(r["provider"] and r["provider"].get("id") == pid and r["state"] not in ("completed", "failed", "cancelled")
                                 for r in stack.reviews.values())
                    if prov.get("is_default") or active:
                        return self.err(409, "PROVIDER_IN_USE", "provider is the default or used by an active review")
                    stack.providers.remove(prov); stack.deleted_providers.append(pid)
                    return self.send(204, "")
                return self.err(404, "NOT_FOUND", path)
            if path == "/api/reviews":
                with stack.lock:
                    if m == "GET":
                        items = sorted(stack.reviews.values(), key=lambda r: r["created_at"], reverse=True)
                        return self.send(200, {"items": items, "page": 1, "page_size": 20, "total": len(items)})
                    b = self.body()
                    stack.created.append(b)
                    prov = next((p for p in stack.providers if p["id"] == b.get("provider_id")
                                 or (not b.get("provider_id") and p.get("is_default"))), None)
                    if prov and prov["kind"] == "cloud" and not b.get("ack_cloud"):
                        return self.send(422, {"error": {"code": "CLOUD_ACK_REQUIRED", "message": "cloud provider: resend with ack_cloud=true"}})
                    return self.send(201, stack.new_review(b["repo_url"], b.get("depth", "quick"), b.get("provider_id")))
            mt = re.fullmatch(r"/api/reviews/([0-9a-f-]+)(?:/(\w+(?:\.\w+)?))?", path)
            if not mt or mt.group(1) not in stack.reviews:
                return self.err(404, "NOT_FOUND", "no such review")
            rid, sub = mt.group(1), mt.group(2)
            r = stack.reviews[rid]
            if sub == "progress":  # long-lived stream: must not hold stack.lock (it would block every other call)
                return self.sse(rid)
            with stack.lock:
                if sub is None:
                    return self.send(200, r)
                if sub == "files" and m == "GET":
                    items = stack.files(rid)
                    return self.send(200, {"items": items, "page": 1, "page_size": 100, "total": len(items)})
                if sub == "attempts" and m == "GET":
                    return self.send(200, stack.attempts(rid))
                if m == "POST" and sub in ("pause", "resume", "cancel", "retry"):
                    allowed = {"pause": PAUSABLE, "resume": RESUMABLE, "cancel": CANCELLABLE, "retry": {"failed"}}[sub]
                    if r["state"] not in allowed:
                        return self.err(409, "CONFLICT", f"cannot {sub} from {r['state']}")
                    if sub == "pause":
                        r["state"] = "paused"
                    elif sub == "resume":
                        r["state"] = "queued"
                    elif sub == "cancel":
                        r["state"] = "cancelled"; r["finished_at"] = now()
                    else:
                        r.update(state="queued", attempt=1, budget_no=r["budget_no"] + 1)
                    r["updated_at"] = now()
                    return self.send(202, {"id": rid, "state": r["state"]})
                if sub in ("report.json", "report.md"):
                    if rid in stack.expired:
                        return self.err(410, "REPORT_EXPIRED", "removed by retention cleanup")
                    if r["state"] != "completed" and not (r["state"] in ("failed", "cancelled") and stack.partial.get(rid)):
                        return self.err(409, "CONFLICT", "no report")
                    j, md = stack.report(rid)
                    return self.send(200, j) if sub == "report.json" else self.send(200, md, "text/markdown")
            return self.err(404, "NOT_FOUND", path)

        def sse(self, rid):
            stack.sse_subscribers += 1
            if "text/event-stream" not in (self.headers.get("Accept") or ""):
                r = stack.reviews[rid]
                return self.send(200, {"review_id": rid, "files_done": r["files_done"], "files_total": r["files_total"]})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = None
            for _ in range(600):
                r = stack.reviews[rid]
                cur = (r["state"], r["files_done"], r["files_total"])
                if cur != last:
                    last = cur
                    try:
                        self.wfile.write(f"event: progress\ndata: {json.dumps({'files_done': r['files_done'], 'files_total': r['files_total']})}\n\n".encode())
                        self.wfile.write(f"event: state\ndata: {json.dumps({'state': r['state'], 'attempt': r['attempt'], 'budget_no': r['budget_no']})}\n\n".encode())
                        if r["state"] in ("completed", "failed", "cancelled"):
                            self.wfile.write(f"event: done\ndata: {json.dumps({'state': r['state']})}\n\n".encode())
                            self.wfile.flush(); break
                        self.wfile.flush()
                    except OSError:
                        break
                time.sleep(0.05)
            self.close_connection = True

        def mock(self, m, path):
            parts = path.split("/")
            if parts[2] == "set" and m == "POST":
                rid, b = parts[3], self.body()
                with stack.lock:
                    stack.partial[rid] = b.pop("partial", stack.partial.get(rid, False))
                    stack.reviews[rid].update(b, updated_at=now())
                return self.send(200, stack.reviews[rid])
            if parts[2] == "calls":
                return self.send(200, stack.calls)
            return self.send(404, {})

    return H


def serve(dist, port=0):
    stack = Stack(os.path.abspath(dist))
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(stack))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, stack
