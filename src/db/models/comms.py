import uuid
from datetime import datetime
from src.db.models.base import Base
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Text,
    Integer,
    UniqueConstraint,
    Boolean,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from src.schemas.email import EmailStatus

class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({', '.join(repr(s.value) for s in EmailStatus)})",
            name="email_outbox_status_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="email_outbox_tenant_id_idempotency_key_key",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    dev_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )