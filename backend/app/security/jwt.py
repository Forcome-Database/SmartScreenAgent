from datetime import datetime, timedelta, timezone
import jwt
from backend.app.config import get_settings

_settings = get_settings()


def create_access_token(claims: dict, expires_hours: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        hours=expires_hours or _settings.JWT_EXPIRE_HOURS
    )
    payload = {**claims, "exp": expire}
    return jwt.encode(payload, _settings.JWT_SECRET_KEY, algorithm=_settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _settings.JWT_SECRET_KEY, algorithms=[_settings.JWT_ALGORITHM])
    except jwt.PyJWTError as e:
        raise ValueError(f"Invalid token: {e}") from e
