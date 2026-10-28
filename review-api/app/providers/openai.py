from .base import Provider


class OpenAIProvider(Provider):
    name = "openai"
    is_cloud = True
    needs_key = True
    default_base_url = 'https://api.openai.com/v1'
    host_hint = 'api.openai.com'
