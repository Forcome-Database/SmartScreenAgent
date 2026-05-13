from __future__ import annotations

import hashlib
import re

from backend.app.security.crypto import (
    decrypt_pii as _decrypt_pii,
    encrypt_pii as _encrypt_pii,
)

_NON_DIGIT = re.compile(r"\D+")


def encrypt_pii(value: str | None) -> str | None:
    if value is None:
        return None
    return _encrypt_pii(value)


def decrypt_pii(cipher: str | None) -> str | None:
    if cipher is None:
        return None
    return _decrypt_pii(cipher)


def _normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return _NON_DIGIT.sub("", phone)


def compute_pii_hash(*, name: str | None, phone: str | None) -> str:
    payload = f"{(name or '').strip()}|{_normalize_phone(phone)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
