import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.db.models.base import Base


class CorrectionFactor(Base):
    __tablename__ = "correction_factors"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "kind",
            "scope_key",
            name="correction_factors_tenant_id_kind_scope_key_key",
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
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    clamp_low: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    clamp_high: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    half_life: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    consecutive_clamps: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FactorHistory(Base):
    __tablename__ = "factor_histories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    correction_factor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("correction_factors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    old_value: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    new_value: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    observation: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    clamped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)