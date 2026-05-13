import pytest

from backend.app.security.crypto import decrypt_pii, encrypt_pii


def test_encrypt_decrypt_roundtrip():
    plain = "张三 13800138000"
    cipher = encrypt_pii(plain)
    assert cipher != plain
    assert decrypt_pii(cipher) == plain


def test_encrypt_empty_string():
    cipher = encrypt_pii("")
    assert decrypt_pii(cipher) == ""


def test_decrypt_invalid_raises():
    with pytest.raises(ValueError):
        decrypt_pii("not-real-cipher")
