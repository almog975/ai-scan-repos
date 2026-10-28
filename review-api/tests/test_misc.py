from contract import check


def test_repo_check(client, H):
    r = client.get("/repos/check", params={"url": "https://github.com/a/b"}, headers=H)
    assert r.status_code == 200
    check("RepoCheck", r.json())
    assert r.json()["ref"] == "main" and r.json()["size_mb"] == 2.0
    r = client.get("/repos/check", params={"url": "https://github.com/a/b", "ref": "v1.2"}, headers=H)
    assert r.json()["ref"] == "v1.2"
    r = client.get("/repos/check", params={"url": "https://github.com/x/y"}, headers=H)
    assert r.status_code == 404
    check("Error", r.json())
    r = client.get("/repos/check", params={"url": "https://evil.com/a/b"}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/repos/check", params={"url": "https://github.com/a/b"}).status_code == 401


def test_settings(client, A, H):
    r = client.get("/admin/settings", headers=A)
    check("Settings", r.json())
    new = {"default_provider_id": None, "max_concurrent_reviews": 1, "max_repo_size_mb": 100}
    r = client.put("/admin/settings", json=new, headers=A)
    assert r.status_code == 200 and r.json() == new
    check("Settings", r.json())
    assert client.get("/admin/settings", headers=A).json() == new
    for bad in ({**new, "unknown": 1}, {**new, "max_concurrent_reviews": 0}, {"max_repo_size_mb": 5}):
        r = client.put("/admin/settings", json=bad, headers=A)
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.put("/admin/settings", json={**new, "default_provider_id": "00000000-0000-0000-0000-000000000000"}, headers=A)
    assert r.status_code == 404
    assert client.get("/admin/settings", headers=H).status_code == 403
    assert client.put("/admin/settings", json=new, headers=H).status_code == 403
    # max_concurrent_reviews=1 is enforced by POST /reviews
    assert client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "quick"}, headers=H).status_code == 201
    assert client.post("/reviews", json={"repo_url": "https://github.com/a/b", "depth": "quick"}, headers=H).status_code == 429
