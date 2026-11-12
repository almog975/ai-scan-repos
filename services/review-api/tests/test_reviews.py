from app import db as dbmod
from app.models import AIProvider, ProviderKind, ReviewAttempt
from contract import check

URL = "https://github.com/a/b"


def mkprov(kind="cloud", enabled=True):
    with dbmod.session_factory()() as db:
        p = AIProvider(name="p", kind=ProviderKind(kind), model="m", enabled=enabled)
        db.add(p)
        db.commit()
        return p.id


def test_create_get_list(client, H, A, launcher):
    r = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers=H)
    assert r.status_code == 201, r.text
    body = r.json()
    check("Review", body)
    assert (body["state"], body["attempt"], body["budget_no"], body["ref"]) == ("queued", 1, 1, "HEAD")
    assert len(body["commit_sha"]) == 40 and r.headers["location"] == f"/reviews/{body['id']}"
    assert launcher.launched == [(body["id"], 1)]
    with dbmod.session_factory()() as db:
        a = db.query(ReviewAttempt).one()
        assert (a.budget_no, a.n, a.job_name) == (1, 1, f"scan-{body['id']}-a1")
    g = client.get(f"/reviews/{body['id']}", headers=H)
    assert g.status_code == 200
    check("Review", g.json())
    assert client.get(f"/reviews/{body['id']}", headers=A).status_code == 200  # admin may read
    lst = client.get("/reviews?page=1&page_size=10", headers=H).json()
    check("ReviewList", lst)
    assert lst["total"] == 1 and lst["items"][0]["id"] == body["id"]
    assert client.get("/reviews?state=completed", headers=H).json()["total"] == 0
    assert client.get("/reviews", headers=A).json()["total"] == 0  # list = own reviews
    r = client.get("/reviews?page_size=101", headers=H)
    assert r.status_code == 400
    check("Error", r.json())


def test_validation_and_closed_body(client, H):
    for body in ({"repo_url": "https://gitlab.com/a/b", "depth": "quick"},
                 {"repo_url": URL, "depth": "extreme"},
                 {"repo_url": URL},
                 {"repo_url": URL, "depth": "quick", "extra": 1}):
        r = client.post("/reviews", json=body, headers=H)
        assert r.status_code == 400, body
        check("Error", r.json())
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_cloud_ack(client, H):
    pid = mkprov("cloud")
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": pid}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED"
    check("Error", r.json())
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": pid, "ack_cloud": True}, headers=H)
    assert r.status_code == 201
    assert r.json()["provider"] == {"id": pid, "kind": "cloud", "cloud_warning": True}
    onprem = mkprov("onprem")
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": onprem}, headers=H)
    assert r.status_code == 201 and r.json()["provider"]["cloud_warning"] is False


def test_404_409_429(client, H, A):
    assert client.post("/reviews", json={"repo_url": "https://github.com/x/y", "depth": "quick"}, headers=H).status_code == 404
    off = mkprov("onprem", enabled=False)
    r = client.post("/reviews", json={"repo_url": URL, "depth": "quick", "provider_id": off}, headers=H)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.post("/reviews", json={"repo_url": URL, "ref": "fixed", "depth": "quick"}, headers=H).status_code == 201
    r = client.post("/reviews", json={"repo_url": URL, "ref": "fixed", "depth": "quick"}, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"
    assert client.post("/reviews", json={"repo_url": "https://github.com/big/repo", "depth": "quick"}, headers=H).status_code == 400
    for _ in range(2):
        assert client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers=H).status_code == 201
    r = client.post("/reviews", json={"repo_url": URL, "depth": "quick"}, headers=H)
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED" and r.headers["retry-after"]
    assert client.get("/reviews/00000000-0000-0000-0000-000000000000", headers=H).status_code == 404
    assert client.get("/reviews/not-a-uuid", headers=H).status_code == 404  # contract: only 404 documented
    assert client.get("/reviews", headers={}).status_code == 401
