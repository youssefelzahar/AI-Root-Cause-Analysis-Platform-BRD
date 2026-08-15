import pytest

from app.core.security import SecretDecryptionError, decrypt_secret, encrypt_secret, rotate_secret

SECRET = "sup3r-s3cret-Passw0rd!"


def test_roundtrip():
    assert decrypt_secret(encrypt_secret(SECRET)) == SECRET


def test_ciphertext_never_contains_the_plaintext():
    token = encrypt_secret(SECRET)
    assert SECRET not in token
    assert token.startswith("v1:")


def test_same_plaintext_encrypts_differently_each_time():
    assert encrypt_secret(SECRET) != encrypt_secret(SECRET)


def test_empty_secret_is_refused():
    with pytest.raises(ValueError):
        encrypt_secret("")


def test_garbage_token_raises_a_typed_error():
    with pytest.raises(SecretDecryptionError):
        decrypt_secret("v1:not-a-real-token")


def test_rotation_preserves_the_secret():
    assert decrypt_secret(rotate_secret(encrypt_secret(SECRET))) == SECRET
