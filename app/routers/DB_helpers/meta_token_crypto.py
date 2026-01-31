# app/routers/meta_token_crypto.py
from __future__ import annotations

import hashlib
from cryptography.fernet import Fernet


class MetaTokenCrypto:
    def __init__(self, fernet_key: str | bytes) -> None:
        key_bytes = fernet_key.encode("utf-8") if isinstance(fernet_key, str) else fernet_key
        self.fernet = Fernet(key_bytes)

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        return self.fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def fingerprint(self, plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
