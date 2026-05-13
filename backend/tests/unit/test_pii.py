from backend.app.services.parser.pii import (
    encrypt_pii,
    decrypt_pii,
    compute_pii_hash,
)


def test_encrypt_roundtrip():
    cipher = encrypt_pii("张三")
    assert cipher != "张三"
    assert decrypt_pii(cipher) == "张三"


def test_none_passes_through():
    assert encrypt_pii(None) is None
    assert decrypt_pii(None) is None


def test_pii_hash_is_stable_and_normalizes_phone():
    a = compute_pii_hash(name="张三", phone="138-0000-1234")
    b = compute_pii_hash(name="张三", phone="13800001234")
    c = compute_pii_hash(name="张三", phone="13800001234 ")
    assert a == b == c
    assert len(a) == 64


def test_pii_hash_differs_for_different_input():
    a = compute_pii_hash(name="张三", phone="13800001234")
    b = compute_pii_hash(name="李四", phone="13800001234")
    assert a != b
