import base64
import json

import pytest

from app import db as dbmod
from app.crypto import KeyCipher, parse_key
from app.models import AIProvider
from app.providers import ADAPTERS, adapter_name
from contract import check, common_enum

W = {"name": "OpenAI", "kind": "cloud", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "api_key": "sk-test-secret-1234"}


def test_crypto_roundtrip_and_tamper():
    c = KeyCipher("11" * 32)
    ct, nonce = c.encrypt("sk-secret", "pid-1")
    assert b"sk-secret" not in ct and len(nonce) == 12
    assert c.decrypt(ct, nonce, "pid-1") == "sk-secret"
    with pytest.raises(ValueError):
        c.decrypt(ct, nonce, "pid-2")  # AAD bound to provider id
    with pytest.raises(ValueError):
        c.decrypt(bytes([ct[0] ^ 1]) + ct[1:], nonce, "pid-1")
    with pytest.raises(ValueError):
        KeyCipher("22" * 32).decrypt(ct, nonce, "pid-1")
    assert len(parse_key(base64.b64encode(b"k" * 32).decode())) == 32
    for bad in ("", "abcd", "00" * 16):
        with pytest.raises(ValueError):
            parse_key(bad)


def test_admin_crud_and_no_secret_leak(client, A, H):
    r = client.post("/admin/ai-providers", json=W, headers=A)
    assert r.status_code == 201, r.text
    p = r.json()
    check("AIProvider", p)
    assert p["key_masked"] == "****1234"
    with dbmod.session_factory()() as db:
        row = db.get(AIProvider, p["id"])
        assert b"sk-test" not in row.api_key_enc and row.key_nonce
    assert client.post(f"/admin/ai-providers/{p['id']}/test", headers=A).json()["adapter"] == "openai"
    lst = client.get("/admin/ai-providers", headers=A).json()
    for x in lst:
        check("AIProvider", x)
    pub = client.get("/ai-providers", headers=H)
    assert pub.status_code == 200
    for x in pub.json():
        check("ProviderPublic", x)
    for body in (r.text, json.dumps(lst), pub.text):
        assert "sk-test" not in body and "api_key" not in body
    r = client.patch(f"/admin/ai-providers/{p['id']}", json={"api_key": None}, headers=A)
    assert r.status_code == 200 and r.json()["key_masked"] is None
    r = client.post(f"/admin/ai-providers/{p['id']}/test", headers=A)
    assert r.status_code == 502
    check("Error", r.json())
    r = client.patch(f"/admin/ai-providers/{p['id']}", json={"enabled": False}, headers=A)
    assert r.json()["enabled"] is False and client.get("/ai-providers", headers=H).json() == []
    assert client.patch(f"/admin/ai-providers/{p['id']}", json={}, headers=A).status_code == 400
    assert client.patch(f"/admin/ai-providers/{p['id']}", json={"kind": "onprem"}, headers=A).status_code == 400
    assert client.delete(f"/admin/ai-providers/{p['id']}", headers=A).status_code == 204
    assert client.delete(f"/admin/ai-providers/{p['id']}", headers=A).status_code == 404


def test_admin_only(client, H):
    for m, path in [("get", "/admin/ai-providers"), ("post", "/admin/ai-providers")]:
        r = getattr(client, m)(path, headers=H, **({"json": W} if m == "post" else {}))
        assert r.status_code == 403
        check("Error", r.json())
    assert client.get("/admin/ai-providers").status_code == 401
    assert client.get("/ai-providers").status_code == 401


def test_validation(client, A):
    for bad in ({**W, "kind": "saas"}, {**W, "extra": 1}, {k: v for k, v in W.items() if k != "model"},
                {**W, "base_url": "ftp://x"}):
        r = client.post("/admin/ai-providers", json=bad, headers=A)
        assert r.status_code == 400, bad
        check("Error", r.json())


def test_eight_adapters_no_bedrock():
    assert len(ADAPTERS) == 8 and "bedrock" not in ADAPTERS
    assert set(ADAPTERS) == {"openai", "anthropic", "gemini", "azure_openai", "mistral", "groq", "ollama",
                             "openai_compatible"}
    assert adapter_name("https://api.anthropic.com", "cloud") == "anthropic"
    assert adapter_name("http://ollama.local:11434", "onprem") == "ollama"
    assert adapter_name("https://myres.openai.azure.com", "cloud") == "azure_openai"
    assert adapter_name("http://vllm:8000/v1", "onprem") == "openai_compatible"
    assert adapter_name(None, "onprem") == "ollama"
    assert {"onprem", "cloud"} == set(common_enum("provider_kind"))


@pytest.mark.parametrize("adapter,given,want", [
    ("openai", None, "https://api.openai.com/v1"),
    ("openai", "https://api.openai.com/v1/", "https://api.openai.com/v1"),
    ("openai_compatible", "http://lm:1234", "http://lm:1234/v1"),
    ("ollama", "http://ollama.local:11434", "http://ollama.local:11434/v1"),
    ("gemini", "https://generativelanguage.googleapis.com", "https://generativelanguage.googleapis.com/v1beta"),
    ("gemini", None, "https://generativelanguage.googleapis.com/v1beta"),
    ("anthropic", "https://api.anthropic.com", "https://api.anthropic.com/v1"),
    ("groq", "https://api.groq.com", "https://api.groq.com/openai/v1"),
    ("mistral", "https://api.mistral.ai/v1", "https://api.mistral.ai/v1"),
    ("azure_openai", "https://r.openai.azure.com/openai/deployments/x", "https://r.openai.azure.com"),
])
def test_base_url_normalized_per_adapter(adapter, given, want):
    from app.providers import normalize_base_url
    assert normalize_base_url(adapter, given) == want


def test_adapters_module_identical_to_worker():
    from pathlib import Path
    here = Path(__file__).resolve().parents[1] / "app/providers/ai_adapters.py"
    worker = Path(__file__).resolve().parents[2] / "scanner-worker/scanner/ai_adapters.py"
    if not worker.exists():
        pytest.skip("scanner-worker not checked out")
    assert here.read_bytes() == worker.read_bytes()


def _mk(client, A, **kw):
    r = client.post("/admin/ai-providers", json={**W, **kw}, headers=A)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_provider_test_makes_real_minimal_call(client, A, ai_http):
    pid = _mk(client, A, kind="onprem", base_url="http://lmstudio.tailnet:1234", api_key=None, name="LM")
    r = client.post(f"/admin/ai-providers/{pid}/test", headers=A)
    assert r.status_code == 200 and r.json()["adapter"] == "openai_compatible"
    req = ai_http.seen[-1]
    assert str(req.url) == "http://lmstudio.tailnet:1234/v1/chat/completions"
    assert json.loads(req.content)["max_tokens"] <= 5
    assert req.extensions["timeout"]["read"] == 10.0


def test_provider_test_errors_are_502_without_secrets(client, A, ai_http):
    import httpx
    pid = _mk(client, A)
    ai_http.state["handler"] = lambda req: httpx.Response(401, json={"error": "bad key sk-test-secret-1234"})
    r = client.post(f"/admin/ai-providers/{pid}/test", headers=A)
    assert r.status_code == 502 and "HTTP 401" in r.json()["error"]["message"] and "sk-test" not in r.text
    check("Error", r.json())

    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)
    ai_http.state["handler"] = boom
    r = client.post(f"/admin/ai-providers/{pid}/test", headers=A)
    assert r.status_code == 502 and "timed out" in r.json()["error"]["message"]


def test_openai_compatible_cloud_kind_needs_ack(client, A, ai_http):
    """kind comes from the DB row: an openai_compatible URL registered as cloud still needs ack_cloud."""
    from app.providers import adapter_name
    pid = _mk(client, A, kind="cloud", base_url="https://llm.example.com/v1", name="hosted-vllm")
    assert adapter_name("https://llm.example.com/v1", "cloud") == "openai_compatible"
    r = client.post("/reviews", json={"repo_url": "https://github.com/o/r", "depth": "quick", "provider_id": pid},
                    headers=A)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CLOUD_ACK_REQUIRED"


def test_readyz_needs_key(settings, launcher, gh):
    from fastapi.testclient import TestClient

    from app.main import create_app
    settings.ai_keys_encryption_key = ""
    with TestClient(create_app(settings, launcher=launcher, github=gh)) as c:
        r = c.get("/readyz")
        assert r.status_code == 503
        check("Error", r.json())
