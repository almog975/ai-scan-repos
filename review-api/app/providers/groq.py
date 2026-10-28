from .base import Provider


class GroqProvider(Provider):
    name = "groq"
    is_cloud = True
    needs_key = True
    default_base_url = 'https://api.groq.com/openai/v1'
    host_hint = 'api.groq.com'
