from .base import Provider


class AnthropicProvider(Provider):
    name = "anthropic"
    is_cloud = True
    needs_key = True
    default_base_url = 'https://api.anthropic.com'
    host_hint = 'api.anthropic.com'
