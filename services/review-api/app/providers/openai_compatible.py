from .base import Provider


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"
    needs_key = False
