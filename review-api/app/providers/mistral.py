from .base import Provider


class MistralProvider(Provider):
    name = "mistral"
    is_cloud = True
    needs_key = True
    default_base_url = 'https://api.mistral.ai/v1'
    host_hint = 'api.mistral.ai'
