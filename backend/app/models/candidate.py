from sqlalchemy import BigInteger, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    name_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    phone_cipher: Mapped[str | None] = mapped_column(Text)
    email_cipher: Mapped[str | None] = mapped_column(Text)
    raw_file_key: Mapped[str | None] = mapped_column(String(512))
    parsed_markdown: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict | None] = mapped_column(JSONB)
    pii_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    __table_args__ = (Index("ix_candidates_source_external", "source", "source_external_id"),)
