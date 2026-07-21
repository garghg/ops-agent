import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.db.models import Template


def slugify(shop_type: str, session: Session) -> str:
    counter = 1
    raw = shop_type
    base = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    slug = f"{base}-v{counter}"

    while session.scalar(select(Template).where(Template.slug == slug)) is not None:
        counter += 1
        slug = f"{base}-v{counter}"

    return slug
