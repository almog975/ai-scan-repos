"""AI provider adapter interface. Phase 1 ships stubs; real HTTP calls land with the scanner AI stage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


class ProviderError(Exception):
    pass


@dataclass
class ProviderConfig:
    base_url: str | None
    model: str
    api_key: str | None


class Provider:
    name: ClassVar[str]
    is_cloud: ClassVar[bool] = True
    needs_key: ClassVar[bool] = True
    default_base_url: ClassVar[str | None] = None

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def test(self) -> dict:
        """Cheap configuration check (stub: no network)."""
        if self.needs_key and not self.cfg.api_key:
            raise ProviderError(f"{self.name}: api_key is required")
        if not (self.cfg.base_url or self.default_base_url):
            raise ProviderError(f"{self.name}: base_url is required")
        return {"ok": True, "adapter": self.name, "model": self.cfg.model}

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        raise NotImplementedError(f"{self.name} adapter is a Phase-1 stub")
