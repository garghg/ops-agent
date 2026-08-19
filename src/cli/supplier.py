from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from sqlalchemy import func, select

from src.cli.context import get_tenant
from src.db.models import (
    InventoryItem,
    Supplier,
    SupplierItem,
    SupplierItemCostHistory,
)
from src.services.config_services import resolve_config
from src.services.cost_service import compute_newsvendor_ratios

app = typer.Typer()
console = Console()


@app.command()
def list():
    session, tenant = get_tenant()

    suppliers = session.scalars(
        select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.name)
    ).all()

    if not suppliers:
        console.print("[yellow]No suppliers.[/yellow]")
        return

    table = Table(title=f"Suppliers -- {tenant.name}")
    table.add_column("Name")
    table.add_column("Email")
    table.add_column("Lead Time", justify="right")
    table.add_column("Cutoff", justify="right")
    table.add_column("Min Order", justify="right")
    table.add_column("Items", justify="right")
    table.add_column("Active")

    for s in suppliers:
        item_count = session.scalar(
            select(func.count())
            .select_from(SupplierItem)
            .where(SupplierItem.supplier_id == s.id)
        )

        table.add_row(
            s.name,
            s.email,
            f"{s.lead_time_days}d",
            f"{s.order_cutoff_hours}h",
            f"${s.minimum_order_value}" if s.minimum_order_value else "--",
            str(item_count),
            "[green]✓[/green]" if s.is_active else "[red]✗[/red]",
        )

    console.print(table)


@app.command()
def add():
    session, tenant = get_tenant()

    name = Prompt.ask("Supplier name")

    existing = session.scalar(
        select(Supplier)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.name == name)
    )
    if existing:
        console.print(f"[red]'{name}' already exists.[/red]")
        return

    email = Prompt.ask("Order email")
    while not email:
        console.print("[red]Email is required.[/red]")
        email = Prompt.ask("Order email")

    while True:
        try:
            lead_time = int(Prompt.ask("Lead time (days)"))
            if lead_time > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except ValueError:
            console.print("[red]Invalid number.[/red]")

    while True:
        try:
            cutoff = int(
                Prompt.ask(
                    "Supplier order cutoff hour (24 hr format; enter 24 or press enter for none)"
                )
            )
            if not cutoff:
                cutoff = 24
                break
            if cutoff > 0:
                cutoff = max(0, min(cutoff, 24))
                console.print(f"Clamped cutoff to 0-24: {cutoff}")   
                break
            console.print("[red]Must be positive.[/red]")
        except ValueError:
            console.print("[red]Invalid number.[/red]")

    min_order_raw = Prompt.ask("Minimum order value (press enter for none)", default="")
    min_order = Decimal(min_order_raw) if min_order_raw else None

    supplier = Supplier(
        tenant_id=tenant.id,
        name=name,
        email=email,
        lead_time_days=lead_time,
        order_cutoff_hours=cutoff,
        minimum_order_value=min_order,
    )
    session.add(supplier)
    session.commit()

    console.print(f"[green]✓ Added supplier '{name}'[/green]")


@app.command()
def edit():
    session, tenant = get_tenant()

    suppliers = session.scalars(
        select(Supplier).where(Supplier.tenant_id == tenant.id).order_by(Supplier.name)
    ).all()

    if not suppliers:
        console.print("[yellow]No suppliers.[/yellow]")
        return

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

    console.print(f"\n[bold]{supplier.name}[/bold]")
    console.print(f"  Email: {supplier.email}")
    console.print(f"  Lead time: {supplier.lead_time_days} days")
    console.print(f"  Cutoff: {supplier.order_cutoff_hours} hours")
    console.print(
        f"  Min order: ${supplier.minimum_order_value}"
        if supplier.minimum_order_value
        else "  Min order: none"
    )
    console.print(f"  Active: {supplier.is_active}")
    console.print("\n  Press enter to keep current value.\n")

    email = Prompt.ask(f"  Email [{supplier.email}]", default="")
    if email:
        supplier.email = email

    lead_time = Prompt.ask(f"  Lead time days [{supplier.lead_time_days}]", default="")
    if lead_time:
        supplier.lead_time_days = int(lead_time)

    cutoff = Prompt.ask(f"  Cutoff hours [{supplier.order_cutoff_hours}]", default="")
    if cutoff:
        cutoff = max(0, min(int(cutoff), 24))
        console.print(f"Clamped cutoff to 0-24: {cutoff}") 
        supplier.order_cutoff_hours = int(cutoff)

    min_order = Prompt.ask(
        f"  Min order [{supplier.minimum_order_value or 'none'}]", default=""
    )
    if min_order:
        supplier.minimum_order_value = (
            Decimal(min_order) if min_order != "none" else None
        )

    active = Prompt.ask(f"  Active [{supplier.is_active}] [y/n]", default="")
    if active and active.lower() == "y":
        supplier.is_active = True
    else:
        supplier.is_active = False
        console.print("[green]✓ Deactivated Supplier[/green]")

    session.commit()
    console.print(f"[green]✓ Updated '{supplier.name}'[/green]")


@app.command("link-item")
def link_item():
    session, tenant = get_tenant()

    suppliers = session.scalars(
        select(Supplier)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.is_active == True)
        .order_by(Supplier.name)
    ).all()

    if not suppliers:
        console.print("[yellow]No active suppliers.[/yellow]")
        return

    console.print("\n[bold]Suppliers:[/bold]")
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

    # Show already-linked items
    linked = session.execute(
        select(SupplierItem, InventoryItem)
        .join(InventoryItem, SupplierItem.inventory_item_id == InventoryItem.id)
        .where(SupplierItem.supplier_id == supplier.id)
        .where(SupplierItem.tenant_id == tenant.id)
    ).all()

    linked_ids = {si.inventory_item_id for si, _ in linked}

    if linked:
        console.print(f"\n  Already linked to {supplier.name}:")
        for si, inv in linked:
            console.print(
                f"    {inv.name} -- ${si.cost_per_unit}/{inv.unit} (pack: {si.pack_size})"
            )

    # Show unlinked items
    unlinked = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.id.not_in(linked_ids) if linked_ids else True)
        .order_by(InventoryItem.name)
    ).all()

    if not unlinked:
        console.print(
            f"\n[yellow]All inventory items already linked to {supplier.name}.[/yellow]"
        )
        return

    console.print("\n[bold]Available items:[/bold]")
    for i, item in enumerate(unlinked, 1):
        console.print(f"  [{i}] {item.name} ({item.unit})")

    while True:
        try:
            idx = int(Prompt.ask("\nSelect item to link"))
            if 1 <= idx <= len(unlinked):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    inv_item = unlinked[idx - 1]

    while True:
        try:
            pack_size = Decimal(Prompt.ask(f"Pack size ({inv_item.unit})"))
            if pack_size > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    while True:
        try:
            cost = Decimal(Prompt.ask(f"Cost per {inv_item.unit}"))
            if cost > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    sku = Prompt.ask("Supplier's product code / SKU (leave blank for none)", default="")

    session.add(
        SupplierItem(
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            inventory_item_id=inv_item.id,
            pack_size=pack_size,
            cost_per_unit=cost,
            sku=sku or None,
        )
    )
    session.commit()

    console.print(
        f"[green]✓ Linked {inv_item.name} to {supplier.name} "
        f"(${cost}/{inv_item.unit}, pack: {pack_size})[/green]"
    )


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

    console.print(f"\n[bold]{supplier.name} -- Items[/bold]\n")
    for i, (si, inv) in enumerate(si_rows, 1):
        console.print(
            f"  [{i}] {inv.name} -- ${si.cost_per_unit}/{inv.unit} "
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
        f"\n  {inv.name}: ${si.cost_per_unit} → ${new_cost} ({sign}{pct:.1f}%)"
    )

    if not Confirm.ask("  Save?"):
        console.print("[yellow]Cancelled.[/yellow]")
        return

    today = datetime.now(ZoneInfo(tenant.timezone)).date()

    session.add(
        SupplierItemCostHistory(
            tenant_id=tenant.id,
            supplier_item_id=si.id,
            old_cost=si.cost_per_unit,
            new_cost=new_cost,
            effective_date=today,
            trigger_source="manual",
        )
    )

    si.cost_per_unit = new_cost
    inv.cost_per_unit = new_cost

    session.commit()
    ratios = compute_newsvendor_ratios(session, str(tenant.id))
    config = resolve_config(str(tenant.id), session)
    if not config:
        return

    margin_floor = config.alerts.margin_floor
    ratio = ratios.get(inv.id)
    if ratio and ratio < Decimal(str(margin_floor)):
        console.print(
            f"[red]Warning: {inv.name} margin below floor -- ratio {ratio:.2f} (floor: {margin_floor})[/red]"
        )
    console.print(f"[green]Cost updated for {inv.name}.[/green]")
