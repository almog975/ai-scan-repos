from pathlib import Path

from tests.conftest import TOKEN


def test_probes(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    m = client.get("/metrics")
    assert m.status_code == 200 and b"ai_scan_reviews_running" in m.content


def test_auth_required(client):
    assert client.get("/reviews").status_code == 401
    assert client.get("/reviews", headers={"X-Internal-Token": "wrong", "X-User-Id": "u"}).status_code == 401
    assert client.get("/reviews", headers={"X-Internal-Token": TOKEN}).status_code == 401


def test_bad_url_422(client, H):
    assert client.post("/reviews", json={"repo_url": "https://evil.com/a/b"}, headers=H).status_code == 422


def test_lifecycle(client, H, launcher, settings):
    r = client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "brief"}, headers=H)
    assert r.status_code == 201
    rv = r.json()
    rid = rv["id"]
    assert rv["status"] == "running" and rv["attempts"] == 1 and rv["job_name"].startswith("scan-")
    assert launcher.launched == [(rid, 1)]

    # other user cannot see it
    assert client.get(f"/reviews/{rid}", headers={**H, "X-User-Id": "u2"}).status_code == 404
    assert len(client.get("/reviews", headers=H).json()) == 1

    assert client.get(f"/internal/reviews/{rid}", headers={"X-Internal-Token": TOKEN}).json()["depth"] == "brief"
    # worker progress needs internal token
    assert client.post(f"/internal/reviews/{rid}/progress", json={"progress": 10}).status_code == 401
    p = client.post(f"/internal/reviews/{rid}/progress", json={"progress": 40, "message": "scanning"},
                    headers={"X-Internal-Token": TOKEN})
    assert p.json()["progress"] == 40

    # pause / resume
    assert client.post(f"/reviews/{rid}/pause", headers=H).json()["status"] == "paused"
    assert (Path(settings.reports_dir) / rid / "PAUSE").exists()
    assert client.post(f"/reviews/{rid}/pause", headers=H).status_code == 409
    # running heartbeat while paused does not un-pause
    client.post(f"/internal/reviews/{rid}/progress", json={"progress": 41, "status": "running"},
                headers={"X-Internal-Token": TOKEN})
    assert client.get(f"/reviews/{rid}", headers=H).json()["status"] == "paused"
    res = client.post(f"/reviews/{rid}/resume", headers=H).json()
    assert res["status"] == "running" and res["attempts"] == 2
    assert not (Path(settings.reports_dir) / rid / "PAUSE").exists()
    assert client.post(f"/reviews/{rid}/resume", headers=H).status_code == 409

    # report not ready
    assert client.get(f"/reviews/{rid}/report", headers=H).status_code == 404

    d = Path(settings.reports_dir) / rid
    (d / "report.json").write_text('{"summary": "ok"}')
    (d / "report.md").write_text("# Report")
    done = client.post(f"/internal/reviews/{rid}/progress", json={"progress": 99, "status": "completed"},
                       headers={"X-Internal-Token": TOKEN}).json()
    assert done["status"] == "completed" and done["progress"] == 100
    assert client.get(f"/reviews/{rid}/report", headers=H).json() == {"summary": "ok"}
    assert client.get(f"/reviews/{rid}/report.md", headers=H).text == "# Report"
    # terminal status is sticky
    client.post(f"/internal/reviews/{rid}/progress", json={"progress": 5, "status": "failed"},
                headers={"X-Internal-Token": TOKEN})
    assert client.get(f"/reviews/{rid}", headers=H).json()["status"] == "completed"


def test_ai_keys(client, H):
    assert client.put("/ai-keys", json={"provider": "openai", "api_key": "sk-x"}, headers=H).status_code == 204
    assert client.put("/ai-keys", json={"provider": "openai", "api_key": "sk-y"}, headers=H).status_code == 204
    keys = client.get("/ai-keys", headers=H).json()
    assert keys == [{"provider": "openai", "configured": True}]
    assert "sk-" not in str(keys)
