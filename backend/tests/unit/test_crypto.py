import pytest
from backend.app.security.crypto import encrypt_pii, decrypt_pii, hash_pii


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


def test_hash_pii_deterministic():
    h1 = hash_pii("13800138000", "张三")
    h2 = hash_pii("13800138000", "张三")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
