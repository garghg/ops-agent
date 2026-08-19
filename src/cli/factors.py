from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.clock import get_now
from src.db.models import CorrectionFactor, Tenant
from src.services.learning_service import reset_factor

app = typer.Typer()


@app.command()
def list():
    session, tenant = get_tenant()
    console = Console()

    factors = session.scalars(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant.id)
        .order_by(CorrectionFactor.kind, CorrectionFactor.scope_key)
    ).all()

    if not factors:
        console.print("[yellow]No correction factors yet.[/yellow]")
        return

    table = Table(title=f"Correction Factors -- {tenant.name}")
    table.add_column("Kind")
    table.add_column("Scope Key", max_width=12)
    table.add_column("Value", justify="right")
    table.add_column("Clamp", justify="right")
    table.add_column("Evidence", justify="right")
    table.add_column("Consecutive Clamps", justify="right")
    table.add_column("Updated")

    for f in factors:
        table.add_row(
            f.kind,
            str(f.scope_key)[:12],
            f"{float(f.value):.4f}",
            f"[{float(f.clamp_low):.2f}, {float(f.clamp_high):.2f}]",
            str(f.evidence_count),
            str(f.consecutive_clamps),
            f.updated_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command()
def reset(kind: str, scope_key: str):
    session, tenant = get_tenant()
    console = Console()

    factor = session.scalar(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant.id)
        .where(CorrectionFactor.kind == kind)
        .where(CorrectionFactor.scope_key == scope_key)
    )

    if not factor:
        console.print(f"[red]No factor found for {kind}:{scope_key}.[/red]")
        return

    tz = ZoneInfo(session.scalar(select(Tenant.timezone).where(Tenant.id == tenant.id)))
    now = get_now().astimezone(tz=tz)
    today = now.date()

    reset_factor(session, str(tenant.id), kind, scope_key, today)
    console.print(f"[green]✓ Factor {kind}:{scope_key} reset to default.[/green]")
