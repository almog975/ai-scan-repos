from .base import Provider


class GroqProvider(Provider):
    name = "groq"
    needs_key = True
