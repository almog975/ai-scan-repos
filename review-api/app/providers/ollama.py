from .base import Provider


class OllamaProvider(Provider):
    name = "ollama"
    is_cloud = False
    needs_key = False
    default_base_url = 'http://ollama:11434'
    host_hint = ':11434'
