from .base import Provider


class GeminiProvider(Provider):
    name = "gemini"
    is_cloud = True
    needs_key = True
    default_base_url = 'https://generativelanguage.googleapis.com'
    host_hint = 'generativelanguage.googleapis.com'
