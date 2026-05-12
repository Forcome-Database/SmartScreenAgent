import pytest

from backend.app.security.jwt import create_access_token, decode_token


def test_create_and_decode_token():
    token = create_access_token({"sub": "42", "role": "hr"})
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "hr"


def test_decode_invalid_token():
    with pytest.raises(ValueError):
        decode_token("not-a-token")
