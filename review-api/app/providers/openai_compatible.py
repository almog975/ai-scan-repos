from .base import Provider


class OpenAICompatibleProvider(Provider):
    name = "openai_compatible"
    is_cloud = False
    needs_key = False
    default_base_url = None
    host_hint = None
