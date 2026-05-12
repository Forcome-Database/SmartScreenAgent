# backend/app/security/crypto.py
import hashlib

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


def hash_pii(phone: str, name: str) -> str:
    """生成稳定哈希用于去重（不可逆）。

    实现注意：手机号搜索空间只有 ~10^11，攻击者拿到库后可用 GPU 爆破 SHA-256。
    若威胁模型要求抗 DB 读取攻击者，升级到 HMAC-SHA-256 并引入独立 PII_INDEX_KEY env var。
    P1 阶段先用 SHA-256 维持简单；安全审计若提出再升级。
    """
    h = hashlib.sha256()
    h.update(phone.encode("utf-8"))
    h.update(b"|")
    h.update(name.encode("utf-8"))
    return h.hexdigest()
