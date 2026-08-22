import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.clock import get_now
from src.db.models import (
    AutonomyEvent,
    CapabilityState,
    Supplier,
)
from src.schemas.autonomy import AutonomyEventType, AutonomyState

app = typer.Typer()


@app.command()
def status():
    session, tenant = get_tenant()
    console = Console()

    rows = session.execute(
        select(CapabilityState, Supplier.name)
        .join(Supplier, CapabilityState.supplier_id == Supplier.id)
        .where(CapabilityState.tenant_id == tenant.id)
        .order_by(Supplier.name)
    ).all()

    if not rows:
        console.print("[yellow]No autonomy states configured yet.[/yellow]")
        return

    table = Table(title=f"Autonomy Status -- {tenant.name}")
    table.add_column("Supplier")
    table.add_column("State")
    table.add_column("Since")

    for cap, supplier_name in rows:
        table.add_row(
            supplier_name,
            cap.state,
            cap.updated_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command()
def grant(supplier_id: str):
    session, tenant = get_tenant()
    console = Console()

    supplier = session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .where(Supplier.tenant_id == tenant.id)
    )

    if not supplier:
        console.print("[red]No supplier with given id.[/red]")
        return

    cap = session.scalar(
        select(CapabilityState)
        .where(CapabilityState.tenant_id == tenant.id)
        .where(CapabilityState.supplier_id == supplier.id)
    )

    if cap and cap.state == AutonomyState.AUTO_WITHIN_BOUNDS.value:
        console.print(f"[yellow]{supplier.name} is already autonomous.[/yellow]")
        return

    if cap:
        old_state = cap.state
        cap.state = AutonomyState.AUTO_WITHIN_BOUNDS.value
    else:
        old_state = None
        session.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )

    session.add(
        AutonomyEvent(
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            event_type=AutonomyEventType.GRANTED.value,
            from_state=old_state or AutonomyState.PROPOSE_ONLY.value,
            to_state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            reason="Owner granted via CLI",
            created_at=get_now(),
        )
    )

    session.commit()
    console.print(f"[green]✓ Autonomy granted for {supplier.name}.[/green]")


@app.command()
def revoke(supplier_id: str):
    session, tenant = get_tenant()
    console = Console()

    supplier = session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .where(Supplier.tenant_id == tenant.id)
    )

    if not supplier:
        console.print("[red]No supplier with given id.[/red]")
        return

    cap = session.scalar(
        select(CapabilityState)
        .where(CapabilityState.tenant_id == tenant.id)
        .where(CapabilityState.supplier_id == supplier.id)
    )

    if not cap or cap.state == AutonomyState.PROPOSE_ONLY.value:
        console.print(f"[yellow]{supplier.name} is already propose-only.[/yellow]")
        return

    cap.state = AutonomyState.PROPOSE_ONLY.value

    session.add(
        AutonomyEvent(
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            event_type=AutonomyEventType.REVOKED.value,
            from_state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            to_state=AutonomyState.PROPOSE_ONLY.value,
            reason="Owner revoked via CLI",
            created_at=get_now(),
        )
    )

    session.commit()
    console.print(f"[green]✓ Autonomy revoked for {supplier.name}.[/green]")