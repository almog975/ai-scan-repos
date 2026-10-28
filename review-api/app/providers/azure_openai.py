from .base import Provider


class AzureOpenAIProvider(Provider):
    name = "azure_openai"
    needs_key = True
