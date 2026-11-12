"""AES-256-GCM for AI provider keys at rest.

Key: AI_KEYS_ENCRYPTION_KEY (Secret `ai-keys-master`), 32 bytes as base64 or hex.
Nonce: 96-bit random per encryption, stored in ai_providers.key_nonce. AAD = provider id, so a
ciphertext copied to another row does not decrypt.
"""
from __future__ import annotations

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def parse_key(raw: str) -> bytes:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("AI_KEYS_ENCRYPTION_KEY is not set")
    for dec in (bytes.fromhex, lambda s: base64.b64decode(s, validate=True), base64.urlsafe_b64decode):
        try:
            k = dec(raw)
        except (ValueError, binascii.Error):
            continue
        if len(k) == 32:
            return k
    raise ValueError("AI_KEYS_ENCRYPTION_KEY must be 32 bytes (hex or base64)")


class KeyCipher:
    def __init__(self, key: str):
        self._a = AESGCM(parse_key(key))

    def encrypt(self, plaintext: str, aad: str) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return self._a.encrypt(nonce, plaintext.encode(), aad.encode()), nonce

    def decrypt(self, ciphertext: bytes, nonce: bytes, aad: str) -> str:
        try:
            return self._a.decrypt(nonce, ciphertext, aad.encode()).decode()
        except (InvalidTag, ValueError) as exc:
            raise ValueError("cannot decrypt AI key (wrong key or tampered data)") from exc


def mask(last4: str | None) -> str | None:
    return f"****{last4}" if last4 else None
