import uuid
from decimal import Decimal
from datetime import date, datetime
from sqlalchemy import (
    Date,
    ForeignKey,
    Numeric,
    Text,
    TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from src.db.models.base import Base


class WeatherObservation(Base):
    __tablename__ = "weather_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "observation_date",
            "source",
            name="weather_observations_tenant_date_source_key",
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
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    max_temp_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    min_temp_c: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    precipitation_mm: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
