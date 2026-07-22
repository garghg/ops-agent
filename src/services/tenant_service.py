import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.schemas.tenant import ShopType
from src.db.models import Tenant
from tzlocal import get_localzone
from src.services.weather_service import geocode


def slugify(name: str, location: str, session: Session) -> str:
    raw = f"{name} {location}"
    base = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    slug = base
    counter = 2

    while session.scalar(select(Tenant).where(Tenant.slug == slug)) is not None:
        slug = f"{base}-{counter}"
        counter += 1

    return slug


def get_system_timezone() -> str:
    return str(get_localzone())


def create_tenant(
    name: str,
    location: str,
    shop_type: ShopType,
    session: Session,
    template_id: str,
    timezone: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> Tenant:
    if latitude is None or longitude is None:
        longitude, latitude, _ = geocode(location)
    tenant = Tenant(
        name=name,
        location=location,
        slug=slugify(name, location, session),
        timezone=timezone or get_system_timezone(),
        shop_type=shop_type.value,
        template_id=template_id,
        longitude=longitude,
        latitude=latitude
    )
    session.add(tenant)
    return tenant