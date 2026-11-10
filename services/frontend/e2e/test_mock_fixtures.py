"""Mock/demo fixtures stay contract-shaped: summary counts match findings; files, attempts, provider test,
delete 409 PROVIDER_IN_USE and report 410 REPORT_EXPIRED are served like the gateway does."""
import http.cookiejar, json, urllib.error, urllib.request


def client(stack):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(f"{stack.base}/auth/github/login").read()
    csrf = next(c.value for c in jar if c.name == "aiscan_csrf")

    def call(method, path, body=None):
        req = urllib.request.Request(f"{stack.base}{path}", json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"})
        try:
            r = op.open(req); raw = r.read(); return r.status, (json.loads(raw) if raw and "json" in r.headers["Content-Type"] else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    return call


def review(stack, call, **kw):
    s, r = call("POST", "/api/reviews", {"repo_url": "https://github.com/o/r", "depth": "quick", "ack_cloud": False})
    assert s == 201
    stack.reviews[r["id"]].update(kw)
    return r["id"]


def test_summary_matches_findings(stack):
    call = client(stack)
    for state, partial in (("completed", False), ("failed", True)):
        rid = review(stack, call, state=state); stack.partial[rid] = partial
        s, rep = call("GET", f"/api/reviews/{rid}/report.json")
        assert s == 200
        for sev in ("critical", "high", "medium", "low", "info"):
            assert rep["summary"][sev] == sum(f["severity"] == sev for f in rep["findings"]), (state, sev)
        _, md = call("GET", f"/api/reviews/{rid}/report.md")
        assert md.decode().count("\n- ") == len(rep["findings"])


def test_files_and_attempts(stack):
    call = client(stack)
    rid = review(stack, call, state="completed", attempt=2, budget_no=1)
    s, page = call("GET", f"/api/reviews/{rid}/files")
    assert s == 200 and set(page) >= {"items", "page", "page_size", "total"} and page["total"] == len(page["items"])
    assert all(set(f) >= {"path", "status", "findings_count"} and f["status"] in
               ("pending", "scanning", "done", "skipped", "error") for f in page["items"])
    _, rep = call("GET", f"/api/reviews/{rid}/report.json")
    assert sum(f["findings_count"] for f in page["items"]) == len(rep["findings"])
    s, att = call("GET", f"/api/reviews/{rid}/attempts")
    assert s == 200 and [a["n"] for a in att] == [1, 2] and att[-1]["outcome"] == "success" and att[0]["outcome"] == "error"


def test_provider_test_and_delete_in_use(stack):
    call = client(stack)
    stack.providers[:] = [{"id": "p1", "name": "Local", "kind": "onprem", "model": "m", "enabled": True, "is_default": True},
                          {"id": "p2", "name": "Spare", "kind": "onprem", "model": "m2", "enabled": True, "is_default": False},
                          {"id": "p3", "name": "Busy", "kind": "onprem", "model": "m3", "enabled": True, "is_default": False}]
    s, res = call("POST", "/api/admin/ai-providers/p1/test")
    assert s == 200 and res["ok"] is True and set(res) >= {"ok", "latency_ms", "model"}
    stack.provider_test["p2"] = "unreachable"
    s, e = call("POST", "/api/admin/ai-providers/p2/test")
    assert s == 502 and e["error"]["code"] == "AI_PROVIDER_UNREACHABLE"
    assert call("DELETE", "/api/admin/ai-providers/p1")[1]["error"]["code"] == "PROVIDER_IN_USE"  # default
    review(stack, call, state="running", provider={"id": "p3", "name": "Busy", "kind": "onprem"})
    s, e = call("DELETE", "/api/admin/ai-providers/p3")
    assert s == 409 and e["error"]["code"] == "PROVIDER_IN_USE"
    assert call("DELETE", "/api/admin/ai-providers/p2")[0] == 204 and stack.deleted_providers == ["p2"]


def test_report_expired_410(stack):
    call = client(stack)
    rid = review(stack, call, state="completed")
    stack.expired.add(rid)
    for f in ("report.json", "report.md"):
        s, e = call("GET", f"/api/reviews/{rid}/{f}")
        assert s == 410 and e["error"]["code"] == "REPORT_EXPIRED"
