from .base import Provider


class BedrockProvider(Provider):
    name = "bedrock"
    is_cloud = True
    needs_key = True
    default_base_url = None
    host_hint = 'bedrock-runtime.'
