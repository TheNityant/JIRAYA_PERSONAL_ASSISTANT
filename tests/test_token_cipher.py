from cryptography.fernet import Fernet

from app.security.tokens import TokenCipher


def test_token_cipher_roundtrip():
    cipher = TokenCipher(Fernet.generate_key().decode("utf-8"))
    encrypted = cipher.encrypt('{"refresh_token":"secret"}')

    assert "secret" not in encrypted
    assert cipher.decrypt(encrypted) == '{"refresh_token":"secret"}'
