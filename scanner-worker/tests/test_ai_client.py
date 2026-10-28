import json

import httpx
import pytest

from scanner.ai_client import AIClient


def mock(provider_resp):
    seen = {}

    def handler(req: httpx.Request):
        seen["req"] = req
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=provider_resp)
    return httpx.MockTransport(handler), seen


OPENAI_RESP = {"choices": [{"message": {"content": "hi"}}]}


@pytest.mark.parametrize("provider,url", [
    ("openai", "https://api.openai.com/v1/chat/completions"),
    ("groq", "https://api.groq.com/openai/v1/chat/completions"),
    ("mistral", "https://api.mistral.ai/v1/chat/completions"),
])
def test_openai_style(provider, url):
    t, seen = mock(OPENAI_RESP)
    c = AIClient(provider, "m", api_key="k", kind="cloud", transport=t)
    assert c.complete("p") == "hi" and c.is_cloud
    assert str(seen["req"].url) == url and seen["req"].headers["authorization"] == "Bearer k"
    assert seen["body"]["messages"][-1] == {"role": "user", "content": "p"}


def test_openai_compatible_onprem():
    t, seen = mock(OPENAI_RESP)
    c = AIClient("openai_compatible", "qwen", base_url="http://lmstudio.tailnet:1234/v1/", kind="onprem", transport=t)
    assert c.complete("p") == "hi" and not c.is_cloud
    assert str(seen["req"].url) == "http://lmstudio.tailnet:1234/v1/chat/completions"
    assert "authorization" not in seen["req"].headers
    with pytest.raises(ValueError):
        AIClient("openai_compatible", "m")


def test_anthropic():
    t, seen = mock({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]})
    assert AIClient("anthropic", "claude", api_key="k", transport=t).complete("p") == "ab"
    r = seen["req"]
    assert str(r.url).endswith("/v1/messages") and r.headers["x-api-key"] == "k"
    assert r.headers["anthropic-version"] and seen["body"]["max_tokens"] > 0 and "system" in seen["body"]


def test_gemini_key_not_in_url():
    t, seen = mock({"candidates": [{"content": {"parts": [{"text": "g"}]}}]})
    assert AIClient("gemini", "gemini-1.5-pro", api_key="secret", transport=t).complete("p") == "g"
    assert "secret" not in str(seen["req"].url) and seen["req"].headers["x-goog-api-key"] == "secret"
    assert str(seen["req"].url).endswith("/models/gemini-1.5-pro:generateContent")


def test_unknown_and_http_error():
    with pytest.raises(ValueError):
        AIClient("nope", "m")
    t = httpx.MockTransport(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        AIClient("openai", "m", api_key="k", transport=t).complete("p")


def test_kind_from_db_not_adapter():
    """openai_compatible registered as cloud in the DB is cloud; missing kind -> cloud (safe side)."""
    assert AIClient("openai_compatible", "m", base_url="https://llm.example.com", kind="cloud").is_cloud
    assert AIClient("openai_compatible", "m", base_url="http://lm:1234").is_cloud
    assert not AIClient("openai", "m", api_key="k", kind="onprem").is_cloud


def test_retry_429_5xx_with_backoff_and_retry_after(monkeypatch):
    from scanner import ai_client
    slept = []
    monkeypatch.setattr(ai_client, "_sleep", slept.append)
    plan = [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(503), httpx.Response(200, json=OPENAI_RESP)]
    t = httpx.MockTransport(lambda r: plan.pop(0))
    assert AIClient("openai", "m", api_key="k", kind="cloud", transport=t).complete("p") == "hi"
    assert slept == [2.0, ai_client.RETRY_BASE_SECONDS * 2]
    calls = []
    t = httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        AIClient("openai", "m", api_key="k", kind="cloud", transport=t).complete("p")
    assert len(calls) == ai_client.RETRY_TRIES == 3
    calls.clear()
    t = httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        AIClient("openai", "m", api_key="k", kind="cloud", transport=t).complete("p")
    assert len(calls) == 1  # 4xx (not 429) is not retried
