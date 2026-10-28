"""Provider registry: 9 adapters. The adapter is picked from base_url (contract AIProvider has no
adapter field); anything unknown is treated as an OpenAI-compatible endpoint (vLLM, LM Studio, ...)."""
from __future__ import annotations

from urllib.parse import urlparse

from .base import Provider, ProviderConfig, ProviderError
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .azure_openai import AzureOpenAIProvider
from .bedrock import BedrockProvider
from .mistral import MistralProvider
from .groq import GroqProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider

ADAPTERS: dict[str, type[Provider]] = {c.name: c for c in (OpenAIProvider, AnthropicProvider, GeminiProvider, AzureOpenAIProvider, BedrockProvider, MistralProvider, GroqProvider, OllamaProvider, OpenAICompatibleProvider)}


def adapter_name(base_url: str | None, kind: str) -> str:
    if not base_url:
        return "openai" if kind == "cloud" else "ollama"
    netloc = urlparse(base_url).netloc.lower()
    for name, cls in ADAPTERS.items():
        hint = getattr(cls, "host_hint", None)
        if hint and hint in netloc:
            return name
    return "openai_compatible"


def adapter_for(base_url: str | None, kind: str, model: str, api_key: str | None) -> Provider:
    return ADAPTERS[adapter_name(base_url, kind)](ProviderConfig(base_url, model, api_key))


__all__ = ["ADAPTERS", "Provider", "ProviderConfig", "ProviderError", "adapter_for", "adapter_name"]
