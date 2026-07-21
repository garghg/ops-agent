from sqlalchemy import select
from sqlalchemy.orm import Session
from src.schemas.template import TemplateConfig
from src.db.models import Template, Tenant, TenantConfig


def deep_merge(base: dict, overrides: dict) -> dict:
    result = base.copy()
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_config(tenant_id: str, session: Session) -> TemplateConfig:
    tenant_row = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant_row:
        raise ValueError(f"Tenant {tenant_id} not found")

    template_row = session.scalar(
        select(Template).where(Template.id == tenant_row.template_id)
    )
    if not template_row:
        raise ValueError(
            f"Template {tenant_row.template_id} not found for tenant {tenant_id}"
        )

    config_row = session.scalar(
        select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)
    )

    body = template_row.body
    if config_row and config_row.overrides:
        body = deep_merge(body, config_row.overrides)

    return TemplateConfig(**body)
