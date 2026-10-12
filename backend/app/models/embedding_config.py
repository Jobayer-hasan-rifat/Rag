import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmbeddingConfigRecord(Base):
    """One embedding configuration (model, revision, prefixes, ...) ever used to embed chunks.

    Vectors are only comparable within one configuration, so every vector references its
    configuration and a search only ever sees vectors of the active one.
    """

    __tablename__ = "embedding_configs"
    __table_args__ = (
        CheckConstraint("dimension > 0", name="dimension_positive"),
        CheckConstraint("max_seq_length > 0", name="max_seq_length_positive"),
        CheckConstraint("metric IN ('cosine')", name="metric_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[str] = mapped_column(String(255), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[str] = mapped_column(String(20), nullable=False)
    normalized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    query_prefix: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    passage_prefix: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    max_seq_length: Mapped[int] = mapped_column(Integer, nullable=False)
    include_heading: Mapped[bool] = mapped_column(Boolean, nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
