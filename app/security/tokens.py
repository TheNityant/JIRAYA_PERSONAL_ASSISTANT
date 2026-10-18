from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    def __init__(self, key: str):
        if not key:
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is required for database-backed connections. "
                "Generate one with Fernet.generate_key() and keep it out of source control."
            )
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Stored provider credentials could not be decrypted.") from exc
