"""Fernet encryption for AI provider keys at rest."""
from cryptography.fernet import Fernet, InvalidToken


class KeyCipher:
    def __init__(self, key: str):
        if not key:
            raise ValueError("AI_KEYS_ENCRYPTION_KEY is not set")
        self._f = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._f.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._f.decrypt(token).decode()
        except InvalidToken as exc:
            raise ValueError("cannot decrypt AI key (wrong encryption key?)") from exc
