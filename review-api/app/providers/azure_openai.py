from .base import Provider


class AzureOpenAIProvider(Provider):
    name = "azure_openai"
    is_cloud = True
    needs_key = True
    default_base_url = None
    host_hint = '.openai.azure.com'
