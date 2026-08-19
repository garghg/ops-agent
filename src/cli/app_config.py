import json

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import TenantConfig
from src.services.config_services import resolve_config

app = typer.Typer()
console = Console()


def _flatten(d: dict, prefix: str = "") -> list[tuple[str, str]]:
    items = []
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            items.extend(_flatten(v, key))
        elif isinstance(v, list):
            items.append((key, json.dumps(v)))
        else:
            items.append((key, str(v)))
    return items


def _get_overridden_keys(overrides: dict, prefix: str = "") -> set[str]:
    keys = set()
    for k, v in overrides.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            keys.update(_get_overridden_keys(v, key))
        else:
            keys.add(key)
    return keys


def _cast_value(value: str):
    if value.lower() in ("true", "false"):
        return value.lower() == "true"

    try:
        return int(value)
    except ValueError:
        pass

    try:
        return float(value)
    except ValueError:
        pass

    return value


@app.command()
def show():
    session, tenant = get_tenant()
    config = resolve_config(str(tenant.id), session)
    resolved = config.model_dump()

    config_row = session.scalar(
        select(TenantConfig).where(TenantConfig.tenant_id == tenant.id)
    )
    overrides = config_row.overrides if config_row else {}
    overridden_keys = _get_overridden_keys(overrides)

    table = Table(title=f"Config -- {tenant.name}")
    table.add_column("Key")
    table.add_column("Value", justify="right")
    table.add_column("Source")

    for key, value in _flatten(resolved):
        source = "[cyan]override[/cyan]" if key in overridden_keys else "default"
        table.add_row(key, value, source)

    console.print(table)


@app.command(name="set")
def set_value(key: str, value: str):
    session, tenant = get_tenant()

    config = resolve_config(str(tenant.id), session)
    flat = dict(_flatten(config.model_dump()))

    if key not in flat:
        console.print(f"[red]Unknown key: {key}[/red]")
        console.print("[yellow]Run 'ops config show' to see available keys.[/yellow]")
        return

    cast = _cast_value(value)

    parts = key.split(".")
    override = {}
    current = override
    for part in parts[:-1]:
        current[part] = {}
        current = current[part]
    current[parts[-1]] = cast

    config_row = session.scalar(
        select(TenantConfig).where(TenantConfig.tenant_id == tenant.id)
    )

    if not config_row:
        config_row = TenantConfig(tenant_id=tenant.id, overrides={})
        session.add(config_row)
        session.flush()

    from src.services.config_services import deep_merge

    config_row.overrides = deep_merge(config_row.overrides, override)
    session.commit()

    console.print(f"[green]✓ {key} = {cast}[/green]")