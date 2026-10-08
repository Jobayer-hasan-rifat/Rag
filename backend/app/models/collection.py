import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin


class Collection(TimestampMixin, Base):
    __tablename__ = "collections"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 255", name="name_length"),
        # Required as the target of the composite foreign keys that keep links same-owner.
        UniqueConstraint("id", "user_id", name="uq_collections_id_user_id"),
        Index("ix_collections_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


# Names are unique per owner, ignoring case.
Index("uq_collections_user_name", Collection.user_id, func.lower(Collection.name), unique=True)
