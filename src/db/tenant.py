import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.db.models import Tenant
from tzlocal import get_localzone


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
