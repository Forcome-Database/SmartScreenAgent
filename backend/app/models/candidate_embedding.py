from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class CandidateEmbedding(Base, TimestampMixin):
    __tablename__ = "candidate_embeddings"

    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
