"""Minimal AI client over httpx (no SDKs).

Providers:
  openai_compatible  on-prem LM Studio / Ollama / vLLM (base_url required, e.g. via Tailscale)
  openai, grok       OpenAI chat-completions API (xAI is OpenAI-compatible)
  anthropic          Messages API
  gemini             generateContent API
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

CLOUD_BASES = {
    "openai": "https://api.openai.com/v1",
    "grok": "https://api.x.ai/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
PROVIDERS = {"openai_compatible", *CLOUD_BASES}
# Used when AI_MODEL is left at its on-prem default but a cloud provider is selected per review.
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "grok": "grok-2-latest", "anthropic": "claude-3-5-sonnet-latest",
                  "gemini": "gemini-1.5-pro"}

SYSTEM_PROMPT = ("You are a senior application-security reviewer. Given scanner findings and code, "
                 "explain real risks, rank them, and give concrete fixes. Be concise. Use Markdown.")


@dataclass
class AIClient:
    provider: str
    model: str
    api_key: str = ""
    base_url: str = ""
    timeout: float = 120.0
    transport: httpx.BaseTransport | None = None

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError(f"unknown AI provider {self.provider!r}")
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("AI_BASE_URL is required for openai_compatible")
        self.base_url = (self.base_url or CLOUD_BASES[self.provider]).rstrip("/")

    @property
    def is_cloud(self) -> bool:
        """True when code/findings leave the private network."""
        return self.provider != "openai_compatible"

    def build_request(self, prompt: str) -> httpx.Request:
        p = self.provider
        if p in ("openai_compatible", "openai", "grok"):
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            return httpx.Request("POST", f"{self.base_url}/chat/completions", headers=headers, json={
                "model": self.model, "temperature": 0.2,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]})
        if p == "anthropic":
            return httpx.Request("POST", f"{self.base_url}/messages",
                                 headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                                 json={"model": self.model, "max_tokens": 2048, "system": SYSTEM_PROMPT,
                                       "messages": [{"role": "user", "content": prompt}]})
        # gemini: key in header, not URL (keeps it out of logs)
        return httpx.Request("POST", f"{self.base_url}/models/{self.model}:generateContent",
                             headers={"x-goog-api-key": self.api_key},
                             json={"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                                   "contents": [{"role": "user", "parts": [{"text": prompt}]}]})

    @staticmethod
    def parse_response(provider: str, data: dict) -> str:
        if provider in ("openai_compatible", "openai", "grok"):
            return data["choices"][0]["message"]["content"]
        if provider == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])

    def complete(self, prompt: str) -> str:
        with httpx.Client(timeout=self.timeout, transport=self.transport) as c:
            r = c.send(self.build_request(prompt))
            r.raise_for_status()
            return self.parse_response(self.provider, r.json())
