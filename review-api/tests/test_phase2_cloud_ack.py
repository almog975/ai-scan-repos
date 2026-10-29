"""Phase 2: a *cloud* provider reached through the generic openai_compatible adapter (e.g. an OpenAI-compatible
SaaS endpoint) is still cloud: review creation needs ack_cloud, and the warning is surfaced."""
import pytest

from app import db as dbmod
from app.models import AIProvider, ProviderKind
from app.providers import adapter_name
from contract import check

URL = "https://github.com/a/b"
BASE = "https://api.together.example/v1"  # no adapter host_hint -> openai_compatible


def mkprov(kind, base_url):
    with dbmod.session_factory()() as db:
        p = AIProvider(name=f"p-{kind}", kind=ProviderKind(kind), model="m", base_url=base_url, enabled=True)
        db.add(p)
        db.commit()
        return p.id


def test_adapter_is_openai_compatible():
    assert adapter_name(BASE, "cloud") == "openai_compatible"


@pytest.mark.parametrize("depth", ["quick", "brief", "deep"])
def test_cloud_via_openai_compatible_requires_ack(client, H, depth):
    pid = mkprov("cloud", BASE)
    r = client.post("/reviews", json={"repo_url": URL, "depth": depth, "provider_id": pid}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED", r.text
    check("Error", r.json())
    r = client.post("/reviews", json={"repo_url": URL, "depth": depth, "provider_id": pid, "ack_cloud": False}, headers=H)
    assert r.status_code == 422
    r = client.post("/reviews", json={"repo_url": URL, "depth": depth, "provider_id": pid, "ack_cloud": True}, headers=H)
    assert r.status_code == 201, r.text
    check("Review", r.json())
    assert r.json()["provider"] == {"id": pid, "kind": "cloud", "cloud_warning": True}
    g = client.get(f"/reviews/{r.json()['id']}", headers=H).json()
    assert g["provider"]["cloud_warning"] is True


def test_cloud_warning_in_public_provider_list(client, H):
    cloud, onprem = mkprov("cloud", BASE), mkprov("onprem", "http://10.0.0.5:8000/v1")
    rows = {p["id"]: p for p in client.get("/ai-providers", headers=H).json()}
    assert rows[cloud]["cloud_warning"] is True and rows[cloud]["kind"] == "cloud"
    assert rows[onprem]["cloud_warning"] is False
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": onprem}, headers=H)
    assert r.status_code == 201 and r.json()["provider"]["cloud_warning"] is False


def test_grok_xai_is_cloud_from_db_row_and_needs_ack(client, A, H, launcher, monkeypatch):
    """Grok (x.ai) has no dedicated adapter: openai_compatible, but kind=cloud comes from the DB row."""
    from app.providers.ai_adapters import normalize_base_url
    xai = "https://api.x.ai/v1"
    assert adapter_name(xai, "cloud") == "openai_compatible"
    assert normalize_base_url("openai_compatible", "https://api.x.ai") == xai
    got = {}
    orig = launcher.launch
    monkeypatch.setattr(launcher, "launch", lambda spec, secret_env=None: got.update(env=secret_env) or orig(spec, secret_env))
    p = client.post("/admin/ai-providers", json={"name": "grok", "kind": "cloud", "base_url": xai,
                                                 "model": "grok-4", "api_key": "xai-123456", "cloud_ack": {"acknowledged": True}}, headers=A)
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": pid}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED"
    assert not got
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": pid, "ack_cloud": True}, headers=H)
    assert r.status_code == 201 and r.json()["provider"] == {"id": pid, "kind": "cloud", "cloud_warning": True}
    assert got["env"]["AI_PROVIDER"] == "openai_compatible" and got["env"]["AI_KIND"] == "cloud"
    assert got["env"]["AI_BASE_URL"] == xai


def test_public_list_marks_default_provider_and_default_cloud_needs_ack(client, H, A):
    """Phase 9: the new-review form needs the default provider's kind (ProviderPublic.is_default)."""
    cloud, onprem = mkprov("cloud", BASE), mkprov("onprem", "http://10.0.0.5:8000/v1")
    rows = client.get("/ai-providers", headers=H).json()
    for row in rows:
        check("ProviderPublic", row)
    assert not any(r["is_default"] for r in rows)
    s = client.get("/admin/settings", headers=A).json()
    assert client.put("/admin/settings", json={**s, "default_provider_id": cloud}, headers=A).status_code == 200
    rows = {p["id"]: p for p in client.get("/ai-providers", headers=H).json()}
    assert rows[cloud]["is_default"] is True and rows[onprem]["is_default"] is False
    # provider_id null -> default (cloud): needs ack_cloud=true
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": None}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED"
    r = client.post("/reviews", json={"repo_url": URL, "depth": "brief", "provider_id": None, "ack_cloud": True}, headers=H)
    assert r.status_code == 201 and r.json()["provider"] == {"id": cloud, "kind": "cloud", "cloud_warning": True}
