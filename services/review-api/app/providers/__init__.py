"""Provider registry: 8 adapters (names = ai_adapters.ADAPTER_NAMES). The adapter is picked from base_url
(the contract AIProvider has no adapter field); unknown hosts are OpenAI-compatible (vLLM, LM Studio, ...).
`kind` (onprem|cloud) always comes from the DB row, never from the adapter."""
from __future__ import annotations

from .ai_adapters import ADAPTER_NAMES, adapter_name, normalize_base_url
from .anthropic import AnthropicProvider
from .azure_openai import AzureOpenAIProvider
from .base import Provider, ProviderConfig, ProviderError
from .gemini import GeminiProvider
from .groq import GroqProvider
from .mistral import MistralProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .openai_compatible import OpenAICompatibleProvider

ADAPTERS: dict[str, type[Provider]] = {c.name: c for c in (
    OpenAIProvider, AnthropicProvider, GeminiProvider, AzureOpenAIProvider, MistralProvider, GroqProvider,
    OllamaProvider, OpenAICompatibleProvider)}
assert tuple(ADAPTERS) == ADAPTER_NAMES


def adapter_for(base_url: str | None, kind: str, model: str, api_key: str | None) -> Provider:
    return ADAPTERS[adapter_name(base_url, kind)](ProviderConfig(base_url, model, api_key))


__all__ = ["ADAPTERS", "Provider", "ProviderConfig", "ProviderError", "adapter_for", "adapter_name",
           "normalize_base_url"]
