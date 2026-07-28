from decimal import Decimal
from math import ceil

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import InventoryItem, POEvent, POLine, PurchaseOrder, Supplier
from src.events.bus import publish_event
from src.schemas.event import EventCategory, ProcurementEventType
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


@app.command()
def approve(po_id: str):
    session, tenant = get_tenant()
    console = Console()

    po = session.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .where(PurchaseOrder.tenant_id == tenant.id)
    )

    if not po:
        console.print("[yellow]No Purchase order with given id.[/yellow]")
        return

    if po.status != POStatus.PROPOSED.value:
        console.print(
            f"[red]Cannot approve -- status is '{po.status}', expected 'proposed'.[/red]"
        )
        return

    po.status = POStatus.APPROVED.value

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=POStatus.PROPOSED.value,
            to_status=POStatus.APPROVED.value,
            changed_by="owner",
            note="Approved via CLI",
        )
    )

    session.commit()
    publish_event(
        EventCategory.PROCUREMENT,
        ProcurementEventType.PO_APPROVED.value,
        "2",
        {"purchase_order_id": str(po.id)},
        str(tenant.id)
    )
    console.print("[green]✓ Purchase order approved.[/green]")


@app.command()
def reject(po_id: str):
    session, tenant = get_tenant()
    console = Console()

    po = session.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .where(PurchaseOrder.tenant_id == tenant.id)
    )

    if not po:
        console.print("[yellow]No Purchase order with given id.[/yellow]")
        return

    if po.status not in (POStatus.PROPOSED.value, POStatus.APPROVED.value):
        console.print(f"[red]Cannot reject -- status is '{po.status}'.[/red]")
        return

    old_status = po.status
    po.status = POStatus.CANCELLED.value

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=old_status,
            to_status=POStatus.CANCELLED.value,
            changed_by="owner",
            note="Rejected via CLI",
        )
    )

    session.commit()
    console.print("[green]✓ Purchase order rejected.[/green]")


@app.command()
def edit(po_id: str):
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

    if po.status != POStatus.PROPOSED.value:
        console.print(f"[red]Cannot edit -- status is '{po.status}'.[/red]")
        return

    po_lines = session.execute(
        select(POLine, InventoryItem)
        .join(InventoryItem, POLine.inventory_item_id == InventoryItem.id)
        .where(POLine.purchase_order_id == po.id)
        .where(POLine.tenant_id == tenant.id)
    ).all()

    if not po_lines:
        console.print("[yellow]Empty Order.[/yellow]")
        return

    show(str(po.id))

    delPO = Prompt.ask("Delete PO [y/n]")

    if delPO == "y":
        old_status = po.status
        po.status = POStatus.CANCELLED.value
        session.add(
            POEvent(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                from_status=old_status,
                to_status=POStatus.CANCELLED.value,
                changed_by="owner",
                note="Cancelled during edit",
            )
        )
        session.commit()
        console.print("[green]✓ Purchase order cancelled.[/green]")
        return

    edits = []
    for line, item in po_lines:
        original = line.quantity_ordered
        new_quantity = Prompt.ask(
            f"[cyan]{item.name}[/cyan] (on hand: {item.quantity_on_hand}, proposed: {line.quantity_ordered} {item.unit}) New amount ['0' to delete line]"
        )
        if new_quantity and new_quantity != "0":
            line.quantity_ordered = Decimal(new_quantity)
            edits.append(
                {"item": item.name, "from": float(original), "to": float(new_quantity)}
            )
        elif new_quantity == "0":
            session.delete(line)
            edits.append({"item": item.name, "from": float(original), "to": 0})

    if po.suggested_topups:
        console.print(
            f"\n[yellow]Order below minimum (${supplier.minimum_order_value:.2f}). Suggested items:[/yellow]"
        )

        for suggested in po.suggested_topups:
            qty = Prompt.ask(
                f"  [cyan]{suggested['name']}[/cyan] (on hand: {suggested['quantity_on_hand']}, "
                f"target: {suggested['target_quantity']} {suggested['unit']}, "
                f"pack size: {suggested['pack_size']}) Quantity to add [enter to skip]"
            )
            if not qty:
                continue

            quantity_ordered = ceil(
                Decimal(qty) / Decimal(str(suggested["pack_size"]))
            ) * Decimal(str(suggested["pack_size"]))

            session.add(
                POLine(
                    tenant_id=tenant.id,
                    purchase_order_id=po.id,
                    inventory_item_id=suggested["inventory_item_id"],
                    supplier_item_id=suggested["supplier_item_id"],
                    quantity_ordered=quantity_ordered,
                    unit_cost=Decimal(str(suggested["cost_per_unit"])),
                )
            )
            session.flush()

            edits.append(
                {"item": suggested["name"], "from": 0, "to": float(quantity_ordered)}
            )
    if not edits:
        console.print("[yellow]No changes made.[/yellow]")
        return

    remaining_lines = session.scalars(
        select(POLine)
        .where(POLine.purchase_order_id == po.id)
        .where(POLine.tenant_id == tenant.id)
    ).all()

    if not remaining_lines:
        po.status = POStatus.CANCELLED.value
        session.add(
            POEvent(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                from_status=POStatus.PROPOSED.value,
                to_status=POStatus.CANCELLED.value,
                changed_by="owner",
                note="All lines removed during edit",
            )
        )
        session.commit()
        console.print("[yellow]All lines removed — order cancelled.[/yellow]")
        return

    po.total_value = sum(l.quantity_ordered * l.unit_cost for l in remaining_lines)

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=POStatus.PROPOSED.value,
            to_status=POStatus.PROPOSED.value,
            changed_by="owner",
            note=f"Edited via CLI: {edits}",
        )
    )

    po.suggested_topups = None
    session.commit()
    console.print(
        f"[green]✓ Purchase order updated. New total: ${po.total_value:.2f}[/green]"
    )
