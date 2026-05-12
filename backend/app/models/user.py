from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    """HR / 管理员用户。

    `dingtalk_userid` 字段存放的是钉钉的 unionId（跨钉钉应用稳定的标识），
    而非 app-scoped 的 openId。见 docs/specs/research/dingtalk-oauth.md。
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dingtalk_userid: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="hr")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
