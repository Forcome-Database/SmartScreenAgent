from sqlalchemy import BigInteger, CheckConstraint, Index, String, Text
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
    raw_file_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    raw_file_content_type: Mapped[str | None] = mapped_column(String(128))
    raw_file_original_name_cipher: Mapped[str | None] = mapped_column(Text)
    parsed_markdown: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict | None] = mapped_column(JSONB)
    pii_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    __table_args__ = (
        Index("ix_candidates_source_external", "source", "source_external_id"),
        CheckConstraint(
            "raw_file_sha256 IS NULL OR char_length(raw_file_sha256) = 64",
            name="ck_candidates_raw_file_sha256_length",
        ),
        CheckConstraint(
            "raw_file_size_bytes IS NULL OR raw_file_size_bytes >= 0",
            name="ck_candidates_raw_file_size_nonnegative",
        ),
    )
