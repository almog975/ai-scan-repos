"""AI provider adapter interface (review-api side). HTTP lives in ai_adapters.py (shared with scanner-worker)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

import httpx

from .ai_adapters import Adapter, normalize_base_url

TEST_TIMEOUT_SECONDS = 10.0
TEST_PROMPT = "Reply with the single word: ok"


class ProviderError(Exception):
    """code: AI_PROVIDER_UNREACHABLE | AI_PROVIDER_AUTH_FAILED | AI_PROVIDER_BAD_RESPONSE (contract 502 codes)."""

    def __init__(self, msg: str, code: str = "AI_PROVIDER_BAD_RESPONSE"):
        super().__init__(msg)
        self.code = code


@dataclass
class ProviderConfig:
    base_url: str | None
    model: str
    api_key: str | None


class Provider:
    name: ClassVar[str]
    needs_key: ClassVar[bool] = True
    transport: ClassVar[httpx.BaseTransport | None] = None  # tests inject httpx.MockTransport

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    @property
    def base_url(self) -> str:
        return normalize_base_url(self.name, self.cfg.base_url)

    def _adapter(self, timeout: float) -> Adapter:
        return Adapter(self.name, self.cfg.model, self.cfg.api_key, self.cfg.base_url, timeout=timeout,
                       transport=self.transport)

    def test(self, timeout: float = TEST_TIMEOUT_SECONDS) -> dict:
        """Real minimal completion (max_tokens=1-ish) with a short timeout."""
        if self.needs_key and not self.cfg.api_key:
            raise ProviderError(f"{self.name}: api_key is required", "AI_PROVIDER_AUTH_FAILED")
        t0 = time.monotonic()
        try:
            a = self._adapter(timeout)
            a.complete(TEST_PROMPT, max_tokens=5, timeout=timeout)
        except ValueError as e:
            raise ProviderError(f"{self.name}: {e}", "AI_PROVIDER_UNREACHABLE") from e
        except httpx.TimeoutException as e:
            raise ProviderError(f"{self.name}: timed out after {timeout:g}s", "AI_PROVIDER_UNREACHABLE") from e
        except httpx.HTTPStatusError as e:
            sc = e.response.status_code
            code = "AI_PROVIDER_AUTH_FAILED" if sc in (401, 403) else "AI_PROVIDER_BAD_RESPONSE"
            raise ProviderError(f"{self.name}: HTTP {sc}", code) from e
        except httpx.HTTPError as e:  # connect / transport errors
            raise ProviderError(f"{self.name}: {type(e).__name__}", "AI_PROVIDER_UNREACHABLE") from e
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name}: {type(e).__name__}", "AI_PROVIDER_BAD_RESPONSE") from e
        return {"ok": True, "latency_ms": int((time.monotonic() - t0) * 1000), "model": self.cfg.model}

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        return self._adapter(120.0).complete(prompt, max_tokens=max_tokens)
