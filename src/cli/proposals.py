import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models.inventory import InventoryItem
from src.db.models.suppliers import POLine, PurchaseOrder, Supplier
from src.schemas.suppliers import POStatus

app = typer.Typer()


@app.command()
def list():
    session, tenant = get_tenant()

    console = Console()

    po_with_suppliers = session.execute(
        select(PurchaseOrder, Supplier)
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .where(PurchaseOrder.tenant_id == tenant.id)
        .where(PurchaseOrder.status.in_([POStatus.APPROVED, POStatus.PROPOSED]))
    ).all()

    if not po_with_suppliers:
        console.print("[yellow]No Purchase orders proposed or approved yet.[/yellow]")
        return

    table = Table(title=f"Purchase Orders -- {tenant.name}")
    table.add_column("PO ID")
    table.add_column("Supplier")
    table.add_column("Status")
    table.add_column("Total Order Value")
    table.add_column("Created At")
    table.add_column("Updated At")
    table.add_column("Notes")

    for po, supplier in po_with_suppliers:
        table.add_row(
            po.id,
            supplier.name,
            po.status,
            f"${po.total_value:.2f}",
            po.created_at.strftime("%Y-%m-%d %H:%M"),
            po.updated_at.strftime("%Y-%m-%d %H:%M"),
            f"Below minimum (${supplier.minimum_order_value:.2f}). {len(po.suggested_topups)} items suggested."
            if po.suggested_topups
            else "",
        )

    console.print(table)


@app.command()
def show(po_id: str):
    session, tenant = get_tenant()

    console = Console()

    result = session.execute(
        select(PurchaseOrder, Supplier)
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .where(PurchaseOrder.id == po_id)
        .where(PurchaseOrder.tenant_id == tenant.id)
    ).first()

    if not result:
        console.print("[yellow]No Purchase order with given id.[/yellow]")
        return

    po, supplier = result

    po_lines = session.execute(
        select(POLine, InventoryItem)
        .join(InventoryItem, POLine.inventory_item_id == InventoryItem.id)
        .where(POLine.purchase_order_id == po.id)
        .where(POLine.tenant_id == tenant.id)
    ).all()

    if not po_lines:
        console.print("[yellow]Empty Order.[/yellow]")
        return

    console.print(f"PO -- {supplier.name} | {po.status}")
    console.print(po.total_value)
    console.print(po.created_at)
    console.print(po.updated_at)

    table = Table(title="Ordered Items")
    table.add_column("Item Name")
    table.add_column("Quantity Ordered")
    table.add_column("Unit Cost")
    table.add_column("Total Item cost")

    for line, item in po_lines:
        table.add_row(
            item.name,
            f"{line.quantity_ordered:.2f}",
            f"${line.unit_cost:.2f}",
            f"${line.quantity_ordered * line.unit_cost:.2f}",
        )

    console.print(table)

    if not po.suggested_topups:
        return

    console.print(
        f"Order below minimum (${supplier.minimum_order_value:.2f}). {len(po.suggested_topups)} items suggested in order."
    )
    suggested_table = Table(title="Suggested Items")
    suggested_table.add_column("Item Name")
    suggested_table.add_column("Current Stock")
    suggested_table.add_column("Target Stock")
    suggested_table.add_column("Pack Size")
    suggested_table.add_column("Cost Per Unit")

    for suggested in po.suggested_topups:
        suggested_table.add_row(
            suggested["name"],
            str(suggested["quantity_on_hand"]),
            str(suggested["target_quantity"]),
            str(suggested["pack_size"]),
            f"${suggested['cost_per_unit']:.2f}",
        )

    console.print(suggested_table)

