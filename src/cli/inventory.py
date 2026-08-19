import uuid
from decimal import Decimal, InvalidOperation

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import func, select

from src.cli.context import get_tenant
from src.db.models import (
    Category,
    InventoryItem,
    InventoryTransaction,
    POLine,
    PurchaseOrder,
)
from src.schemas.inventory import InventoryTransactionType
from src.schemas.suppliers import POStatus

app = typer.Typer()
console = Console()


@app.command()
def list():
    session, tenant = get_tenant()

    items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .order_by(InventoryItem.name)
    ).all()

    if not items:
        console.print("[yellow]No inventory items.[/yellow]")
        return

    on_order = dict(
        session.execute(
            select(
                POLine.inventory_item_id,
                func.coalesce(func.sum(POLine.quantity_ordered), 0),
            )
            .join(PurchaseOrder, POLine.purchase_order_id == PurchaseOrder.id)
            .where(POLine.tenant_id == tenant.id)
            .where(PurchaseOrder.status.in_([POStatus.SENT, POStatus.CONFIRMED]))
            .group_by(POLine.inventory_item_id)
        ).all()
    )

    cat_ids = {item.category_id for item in items}
    categories = session.scalars(
        select(Category).where(Category.id.in_(cat_ids))
    ).all()
    cat_map = {c.id: c.name for c in categories}

    table = Table(title=f"Inventory -- {tenant.name}")
    table.add_column("Item")
    table.add_column("Category")
    table.add_column("On Hand", justify="right")
    table.add_column("On Order", justify="right")
    table.add_column("Reorder Pt", justify="right")
    table.add_column("Target", justify="right")
    table.add_column("Unit")
    table.add_column("Status")

    for item in items:
        ordered = on_order.get(item.id, 0)
        if item.quantity_on_hand <= item.reorder_point:
            status = "[red]LOW[/red]"
        else:
            status = "[green]OK[/green]"

        table.add_row(
            item.name,
            cat_map.get(item.category_id, "-"),
            str(item.quantity_on_hand),
            str(ordered) if ordered else "-",
            str(item.reorder_point),
            str(item.target_quantity),
            item.unit,
            status,
        )

    console.print(table)


@app.command()
def add():
    session, tenant = get_tenant()

    name = Prompt.ask("Item name")

    existing = session.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.name == name)
    )
    if existing:
        console.print(f"[red]'{name}' already exists.[/red]")
        return

    unit = Prompt.ask("Unit (kg, count, liters, etc.)")

    categories = session.scalars(
        select(Category)
        .where(Category.tenant_id == tenant.id)
        .order_by(Category.name)
    ).all()

    if categories:
        console.print("\n[bold]Categories:[/bold]")
        for i, c in enumerate(categories, 1):
            console.print(f"  [{i}] {c.name}")
        console.print(f"  [{len(categories) + 1}] + New category")

        while True:
            try:
                idx = int(Prompt.ask("Category"))
                if 1 <= idx <= len(categories) + 1:
                    break
            except ValueError:
                pass
            console.print("[red]Invalid selection.[/red]")

        if idx <= len(categories):
            category = categories[idx - 1]
        else:
            cat_name = Prompt.ask("New category name")
            category = Category(tenant_id=tenant.id, name=cat_name)
            session.add(category)
            session.flush()
    else:
        cat_name = Prompt.ask("Category name")
        category = Category(tenant_id=tenant.id, name=cat_name)
        session.add(category)
        session.flush()

    while True:
        try:
            reorder_point = Decimal(Prompt.ask("Reorder point"))
            break
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    while True:
        try:
            target = Decimal(Prompt.ask("Target quantity"))
            break
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    while True:
        try:
            cost = Decimal(Prompt.ask("Cost per unit"))
            if cost > 0:
                break
            console.print("[red]Must be positive.[/red]")
        except InvalidOperation:
            console.print("[red]Invalid number.[/red]")

    shelf_life = Prompt.ask("Shelf life in days (press enter for none)", default="")
    shelf_life_days = int(shelf_life) if shelf_life else None

    initial_qty = Prompt.ask("Initial quantity on hand", default="0")
    initial = Decimal(initial_qty)

    item = InventoryItem(
        tenant_id=tenant.id,
        name=name,
        unit=unit,
        category_id=category.id,
        reorder_point=reorder_point,
        target_quantity=target,
        cost_per_unit=cost,
        shelf_life_days=shelf_life_days,
        quantity_on_hand=initial,
    )
    session.add(item)
    session.commit()

    console.print(f"[green]✓ Added '{name}' ({initial} {unit} on hand)[/green]")


@app.command()
def adjust():
    session, tenant = get_tenant()

    search = Prompt.ask("Search item name")
    matches = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.name.ilike(f"%{search}%"))
    ).all()

    if not matches:
        console.print("[red]No items found.[/red]")
        return

    for i, item in enumerate(matches, 1):
        console.print(
            f"  [{i}] {item.name} — {item.quantity_on_hand} {item.unit}"
        )

    while True:
        try:
            idx = int(Prompt.ask("Select item"))
            if 1 <= idx <= len(matches):
                break
        except ValueError:
            pass
        console.print("[red]Invalid selection.[/red]")

    item = matches[idx - 1]

    console.print(f"\n  Current: [bold]{item.quantity_on_hand} {item.unit}[/bold]")
    console.print("  Enter positive to add, negative to subtract")

    while True:
        try:
            qty = Decimal(Prompt.ask("  Adjustment quantity"))
            if qty != 0:
                break
            console.print("[red]  Quantity cannot be zero.[/red]")
        except InvalidOperation:
            console.print("[red]  Invalid number.[/red]")

    reason = Prompt.ask("  Reason")

    if qty > 0:
        tx_type = InventoryTransactionType.ADJUSTMENT_ADD.value
    else:
        tx_type = InventoryTransactionType.ADJUSTMENT_SUB.value

    item.quantity_on_hand += qty

    session.add(
        InventoryTransaction(
            item_id=item.id,
            quantity_change=qty,
            transaction_type=tx_type,
            note=reason,
            event_id=f"cli-adjust-{uuid.uuid4()}",
            tenant_id=tenant.id,
        )
    )

    session.commit()

    sign = "+" if qty > 0 else ""
    console.print(
        f"[green]✓ {item.name}: {sign}{qty} {item.unit} "
        f"(now {item.quantity_on_hand} {item.unit})[/green]"
    )