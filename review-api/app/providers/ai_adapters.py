"""AI adapters over plain httpx (no SDKs). Shared verbatim by review-api (app/providers/ai_adapters.py)
and scanner-worker (scanner/ai_adapters.py); a test keeps the two copies identical.

8 adapters: openai, anthropic, gemini, azure_openai, mistral, groq, ollama, openai_compatible.
`kind` (onprem|cloud) is NOT derived here: it always comes from the provider row in the DB.
Each adapter normalizes its own base_url (adds /v1 or /v1beta when missing)."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx

SYSTEM_PROMPT = ("You are a senior application-security reviewer. Given scanner findings and code, "
                 "explain real risks, rank them, and give concrete fixes. Be concise. Use Markdown.")

# adapter -> (default base_url or None when required, request style, version suffix)
SPECS: dict[str, tuple[str | None, str, str]] = {
    "openai": ("https://api.openai.com", "openai", "/v1"),
    "anthropic": ("https://api.anthropic.com", "anthropic", "/v1"),
    "gemini": ("https://generativelanguage.googleapis.com", "gemini", "/v1beta"),
    "azure_openai": (None, "azure", ""),
    "mistral": ("https://api.mistral.ai", "openai", "/v1"),
    "groq": ("https://api.groq.com/openai", "openai", "/v1"),
    "ollama": ("http://ollama:11434", "openai", "/v1"),
    "openai_compatible": (None, "openai", "/v1"),
}
ADAPTER_NAMES = tuple(SPECS)
HOST_HINTS = {"api.openai.com": "openai", "api.anthropic.com": "anthropic",
              "generativelanguage.googleapis.com": "gemini", ".openai.azure.com": "azure_openai",
              "api.mistral.ai": "mistral", "api.groq.com": "groq", ":11434": "ollama"}
AZURE_API_VERSION = "2024-06-01"


def adapter_name(base_url: str | None, kind: str) -> str:
    """Pick the adapter from base_url (the contract has no adapter field). Unknown host -> openai_compatible."""
    if not base_url:
        return "openai" if kind == "cloud" else "ollama"
    netloc = urlparse(base_url).netloc.lower()
    for hint, name in HOST_HINTS.items():
        if hint in netloc:
            return name
    return "openai_compatible"


def normalize_base_url(adapter: str, base_url: str | None) -> str:
    default, _, suffix = SPECS[adapter]
    u = (base_url or default or "").strip().rstrip("/")
    if not u:
        raise ValueError(f"base_url is required for {adapter}")
    if adapter == "azure_openai":
        i = u.find("/openai")
        return u[:i] if i > 0 else u
    if adapter == "groq" and not urlparse(u).path:
        u += "/openai"
    if suffix and not u.endswith(suffix):
        u += suffix
    return u


class Adapter:
    def __init__(self, adapter: str, model: str, api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 120.0, transport: httpx.BaseTransport | None = None):
        if adapter not in SPECS:
            raise ValueError(f"unknown AI adapter {adapter!r}")
        self.adapter, self.model, self.api_key = adapter, model, api_key or ""
        self.style = SPECS[adapter][1]
        self.base_url = normalize_base_url(adapter, base_url)
        self.timeout, self.transport = timeout, transport

    def build_request(self, prompt: str, max_tokens: int = 2048) -> httpx.Request:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        if self.style == "openai":
            h = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            return httpx.Request("POST", f"{self.base_url}/chat/completions", headers=h, json={
                "model": self.model, "temperature": 0.2, "max_tokens": max_tokens, "messages": msgs})
        if self.style == "azure":
            return httpx.Request("POST", f"{self.base_url}/openai/deployments/{self.model}/chat/completions",
                                 params={"api-version": AZURE_API_VERSION}, headers={"api-key": self.api_key},
                                 json={"temperature": 0.2, "max_tokens": max_tokens, "messages": msgs})
        if self.style == "anthropic":
            return httpx.Request("POST", f"{self.base_url}/messages",
                                 headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                                 json={"model": self.model, "max_tokens": max_tokens, "system": SYSTEM_PROMPT,
                                       "messages": [{"role": "user", "content": prompt}]})
        # gemini: key in a header, never in the URL (keeps it out of logs)
        return httpx.Request("POST", f"{self.base_url}/models/{self.model}:generateContent",
                             headers={"x-goog-api-key": self.api_key},
                             json={"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                                   "generationConfig": {"maxOutputTokens": max_tokens},
                                   "contents": [{"role": "user", "parts": [{"text": prompt}]}]})

    def parse_response(self, data: dict) -> str:
        if self.style in ("openai", "azure"):
            return data["choices"][0]["message"]["content"] or ""
        if self.style == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])

    def complete(self, prompt: str, max_tokens: int = 2048, timeout: float | None = None) -> str:
        with httpx.Client(timeout=timeout or self.timeout, transport=self.transport) as c:
            r = c.send(self.build_request(prompt, max_tokens))
            r.raise_for_status()
            return self.parse_response(r.json())
