"""Phase 4 admin AI provider decisions: cloud_ack, test result, unsaved test, kind PATCH, PROVIDER_IN_USE, 502 codes."""
import httpx
import pytest

from app import db as dbmod
from app.models import Review, State
from contract import check

CLOUD = {"name": "OpenAI", "kind": "cloud", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
         "api_key": "sk-test-secret-1234"}
ONPREM = {"name": "Ollama", "kind": "onprem", "base_url": "http://ollama:11434", "model": "llama3"}


def _err(r, status, code):
    assert r.status_code == status, r.text
    check("Error", r.json())
    assert r.json()["error"]["code"] == code


def test_cloud_create_requires_ack(client, A):
    _err(client.post("/admin/ai-providers", json=CLOUD, headers=A), 422, "CLOUD_ACK_REQUIRED")
    _err(client.post("/admin/ai-providers", json={**CLOUD, "cloud_ack": {"acknowledged": False}}, headers=A),
         422, "CLOUD_ACK_REQUIRED")
    r = client.post("/admin/ai-providers", json={**CLOUD, "cloud_ack": {"acknowledged": True}}, headers=A)
    assert r.status_code == 201
    check("AIProvider", r.json())
    ack = r.json()["cloud_ack"]
    assert ack["acknowledged"] and ack["at"] and ack["by"]["login"] == "admin"


def test_onprem_has_no_ack(client, A):
    r = client.post("/admin/ai-providers", json=ONPREM, headers=A)
    assert r.status_code == 201
    assert r.json()["cloud_ack"] == {"acknowledged": False, "at": None, "by": None}


def test_patch_kind_to_cloud_needs_ack_again(client, A):
    pid = client.post("/admin/ai-providers", json=ONPREM, headers=A).json()["id"]
    _err(client.patch(f"/admin/ai-providers/{pid}", json={"kind": "cloud"}, headers=A), 422, "CLOUD_ACK_REQUIRED")
    r = client.patch(f"/admin/ai-providers/{pid}", json={"kind": "cloud", "cloud_ack": {"acknowledged": True}}, headers=A)
    assert r.status_code == 200 and r.json()["kind"] == "cloud" and r.json()["cloud_ack"]["acknowledged"]
    check("AIProvider", r.json())
    # withdrawing the ack on a cloud provider is rejected
    _err(client.patch(f"/admin/ai-providers/{pid}", json={"cloud_ack": {"acknowledged": False}}, headers=A),
         422, "CLOUD_ACK_REQUIRED")
    r = client.patch(f"/admin/ai-providers/{pid}", json={"kind": "onprem"}, headers=A)
    assert r.json()["cloud_ack"]["acknowledged"] is False
    _err(client.patch(f"/admin/ai-providers/{pid}", json={"kind": "cloud"}, headers=A), 422, "CLOUD_ACK_REQUIRED")
    assert client.patch(f"/admin/ai-providers/{pid}", json={"kind": None}, headers=A).status_code == 400


def test_saved_test_ok_shape(client, A):
    pid = client.post("/admin/ai-providers", json={**CLOUD, "cloud_ack": {"acknowledged": True}}, headers=A).json()["id"]
    r = client.post(f"/admin/ai-providers/{pid}/test", headers=A)
    assert r.status_code == 200
    check("ProviderTestResult", r.json())
    assert set(r.json()) == {"ok", "latency_ms", "model"} and r.json()["ok"] is True


def test_unsaved_test_stores_nothing(client, A, H, ai_http):
    r = client.post("/admin/ai-providers/test", json=CLOUD, headers=A)  # no ack needed: nothing is stored
    assert r.status_code == 200, r.text
    check("ProviderTestResult", r.json())
    assert client.get("/admin/ai-providers", headers=A).json() == []
    assert ai_http.seen and ai_http.seen[-1].headers["authorization"] == "Bearer sk-test-secret-1234"
    assert client.post("/admin/ai-providers/test", json=CLOUD, headers=H).status_code == 403
    assert client.post("/admin/ai-providers/test", json={**CLOUD, "x": 1}, headers=A).status_code == 400


@pytest.mark.parametrize("resp,code", [
    (lambda r: httpx.Response(401, json={}), "AI_PROVIDER_AUTH_FAILED"),
    (lambda r: httpx.Response(403, json={}), "AI_PROVIDER_AUTH_FAILED"),
    (lambda r: httpx.Response(500, json={}), "AI_PROVIDER_BAD_RESPONSE"),
    (lambda r: httpx.Response(200, json={"nope": 1}), "AI_PROVIDER_BAD_RESPONSE"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)), "AI_PROVIDER_UNREACHABLE"),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)), "AI_PROVIDER_UNREACHABLE"),
])
def test_502_codes(client, A, ai_http, resp, code):
    ai_http.state["handler"] = resp
    _err(client.post("/admin/ai-providers/test", json=CLOUD, headers=A), 502, code)


def test_missing_key_is_auth_failed(client, A):
    _err(client.post("/admin/ai-providers/test", json={**CLOUD, "api_key": None}, headers=A), 502,
         "AI_PROVIDER_AUTH_FAILED")


def test_delete_default_or_active_is_409(client, A, users):
    pid = client.post("/admin/ai-providers", json=ONPREM, headers=A).json()["id"]
    s = client.get("/admin/settings", headers=A).json()
    assert client.put("/admin/settings", json={**s, "default_provider_id": pid}, headers=A).status_code == 200
    _err(client.delete(f"/admin/ai-providers/{pid}", headers=A), 409, "PROVIDER_IN_USE")
    client.put("/admin/settings", json={**s, "default_provider_id": None}, headers=A)

    with dbmod.session_factory()() as db:
        r = Review(user_id=users[1], repo_url="https://github.com/a/b", ref="main", commit_sha="a" * 40,
                   depth="quick", state=State.running, provider_id=pid)
        db.add(r)
        db.commit()
        rid = r.id
    _err(client.delete(f"/admin/ai-providers/{pid}", headers=A), 409, "PROVIDER_IN_USE")
    with dbmod.session_factory()() as db:
        db.get(Review, rid).state = State.completed
        db.commit()
    assert client.delete(f"/admin/ai-providers/{pid}", headers=A).status_code == 204
