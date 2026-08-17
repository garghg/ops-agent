from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import (
    InventoryItem,
    Supplier,
    SupplierItem,
    SupplierItemCostHistory,
)

app = typer.Typer()
console = Console()


@app.command("update-item-cost")
def update_cost():
    session, tenant = get_tenant()

    suppliers = session.scalars(
        select(Supplier)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.is_active == True)
    ).all()

    if not suppliers:
        console.print("[yellow]No suppliers found.[/yellow]")
        return

    console.print("\n[bold]Update Supplier Item Cost[/bold]\n")
    for i, s in enumerate(suppliers, 1):
        console.print(f"  [{i}] {s.name}")

    while True:
        try:
            idx = int(Prompt.ask("\nSelect supplier"))
            if 1 <= idx <= len(suppliers):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    supplier = suppliers[idx - 1]

    si_rows = session.execute(
        select(SupplierItem, InventoryItem)
        .join(InventoryItem, SupplierItem.inventory_item_id == InventoryItem.id)
        .where(SupplierItem.tenant_id == tenant.id)
        .where(SupplierItem.supplier_id == supplier.id)
    ).all()

    if not si_rows:
        console.print(f"[yellow]No items found for {supplier.name}.[/yellow]")
        return

    console.print(f"\n[bold]{supplier.name} — Items[/bold]\n")
    for i, (si, inv) in enumerate(si_rows, 1):
        console.print(
            f"  [{i}] {inv.name} — ${si.cost_per_unit}/{inv.unit} "
            f"(pack: {si.pack_size})"
        )

    while True:
        try:
            idx = int(Prompt.ask("\nSelect item"))
            if 1 <= idx <= len(si_rows):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    si, inv = si_rows[idx - 1]

    console.print(f"\n  Current cost: [bold]${si.cost_per_unit}/{inv.unit}[/bold]")

    while True:
        try:
            new_cost = Decimal(Prompt.ask("  New cost"))
            if new_cost > 0:
                break
            console.print("[red]  Cost must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]  Invalid number.[/red]")

    if new_cost == si.cost_per_unit:
        console.print("[yellow]Cost unchanged. Nothing to do.[/yellow]")
        return

    pct = (new_cost - si.cost_per_unit) / si.cost_per_unit * 100
    sign = "+" if pct > 0 else ""
    console.print(
        f"\n  {inv.name}: ${si.cost_per_unit} → ${new_cost} "
        f"({sign}{pct:.1f}%)"
    )

    if not Confirm.ask("  Save?"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    today = datetime.now(ZoneInfo(tenant.timezone)).date()

    session.add(SupplierItemCostHistory(
        tenant_id=tenant.id,
        supplier_item_id=si.id,
        old_cost=si.cost_per_unit,
        new_cost=new_cost,
        effective_date=today,
        trigger_source="manual",
    ))
    si.cost_per_unit = new_cost

    session.commit()
    console.print(f"[green]Cost updated for {inv.name}.[/green]")