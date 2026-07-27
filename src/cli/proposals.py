import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models.suppliers import PurchaseOrder, Supplier
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
    table.add_column("Supplier")
    table.add_column("Status")
    table.add_column("Total Order Value")
    table.add_column("Created At")
    table.add_column("Updated At")
    table.add_column("Notes")

    for po, supplier in po_with_suppliers:
        table.add_row(
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
