# app/routers/meta_token_decrypter.py

from cryptography.fernet import Fernet


class TokenDecryptionError(Exception):
    pass


class MetaTokenDecrypter:
    """
    Responsible ONLY for decrypting Meta access tokens
    stored in the database.

    Single responsibility:
      ciphertext -> plaintext token
    """

    def __init__(self, encryption_key: str):
        """
        encryption_key must be the SAME Fernet key
        used when storing tokens.
        """
        if isinstance(encryption_key, str):
            encryption_key = encryption_key.encode()

        self.fernet = Fernet(encryption_key)

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a token from DB.
        """
        try:
            return self.fernet.decrypt(ciphertext.encode()).decode()
        except Exception as e:
            raise TokenDecryptionError("Failed to decrypt Meta token") from e
