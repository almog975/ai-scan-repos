"""AI provider adapter interface (review-api side). HTTP lives in ai_adapters.py (shared with scanner-worker)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import httpx

from .ai_adapters import Adapter, normalize_base_url

TEST_TIMEOUT_SECONDS = 10.0
TEST_PROMPT = "Reply with the single word: ok"


class ProviderError(Exception):
    pass


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
            raise ProviderError(f"{self.name}: api_key is required")
        try:
            a = self._adapter(timeout)
            a.complete(TEST_PROMPT, max_tokens=5, timeout=timeout)
        except ValueError as e:
            raise ProviderError(f"{self.name}: {e}") from e
        except httpx.TimeoutException as e:
            raise ProviderError(f"{self.name}: timed out after {timeout:g}s") from e
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"{self.name}: HTTP {e.response.status_code}") from e
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.name}: {type(e).__name__}") from e
        return {"ok": True, "adapter": self.name, "model": self.cfg.model, "base_url": a.base_url}

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        return self._adapter(120.0).complete(prompt, max_tokens=max_tokens)
