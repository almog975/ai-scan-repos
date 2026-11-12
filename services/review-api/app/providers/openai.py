from .base import Provider


class OpenAIProvider(Provider):
    name = "openai"
    needs_key = True
