# backend/app/security/crypto.py
from cryptography.fernet import Fernet, InvalidToken

from backend.app.config import get_settings

_settings = get_settings()

if len(_settings.PII_ENCRYPTION_KEY) != 44:
    raise RuntimeError(
        "PII_ENCRYPTION_KEY must be a 44-char base64 Fernet key. "
        "Generate one with: "
        'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

_fernet = Fernet(_settings.PII_ENCRYPTION_KEY.encode())


def encrypt_pii(plaintext: str) -> str:
    """对 PII 字符串加密；空字符串也支持。"""
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_pii(ciphertext: str) -> str:
    """解密回明文；非法 token 抛 ValueError。"""
    try:
        return _fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Invalid ciphertext or wrong key") from e
