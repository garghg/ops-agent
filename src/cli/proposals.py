from datetime import date
from decimal import Decimal
from math import ceil

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table
from sqlalchemy import func, select

from src.cli.context import get_tenant
from src.db.models import (
    InventoryItem,
    POEvent,
    POLine,
    PurchaseOrder,
    Supplier,
    SupplierItem,
)
from src.events.bus import publish_event
from src.schemas.event import EventCategory, InventoryEventType, ProcurementEventType
from src.schemas.inventory import InventoryTransactionType
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.ordering_service import evaluate_demotion

app = typer.Typer()


@app.command()
def list():
    session, tenant = get_tenant()

    console = Console()

    po_with_suppliers = session.execute(
        select(PurchaseOrder, Supplier)
        .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
        .where(PurchaseOrder.tenant_id == tenant.id)
        .where(
            PurchaseOrder.status.in_(
                [
                    POStatus.APPROVED,
                    POStatus.PROPOSED,
                    POStatus.SENT,
                    POStatus.CONFIRMED,
                    POStatus.RECEIVED,
                ]
            )
        )
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
def create(supplier_id: str):
    session, tenant = get_tenant()
    console = Console()

    supplier = session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.is_active.is_(True))
    )

    if not supplier:
        console.print("[yellow]No active supplier with given id.[/yellow]")
        return

    supplier_items = session.execute(
        select(SupplierItem, InventoryItem)
        .join(InventoryItem, SupplierItem.inventory_item_id == InventoryItem.id)
        .where(SupplierItem.supplier_id == supplier.id)
        .where(SupplierItem.tenant_id == tenant.id)
    ).all()

    if not supplier_items:
        console.print("[yellow]No items linked to this supplier.[/yellow]")
        return

    lines = []
    for si, item in supplier_items:
        qty = Prompt.ask(
            f"[cyan]{item.name}[/cyan] (on hand: {item.quantity_on_hand} {item.unit}, "
            f"pack size: {si.pack_size}, cost: ${si.cost_per_unit:.2f}) Quantity [enter to skip]"
        )
        if not qty:
            continue

        quantity_ordered = ceil(Decimal(qty) / si.pack_size) * si.pack_size

        lines.append(
            {
                "inventory_item_id": item.id,
                "supplier_item_id": si.id,
                "quantity_ordered": quantity_ordered,
                "unit_cost": si.cost_per_unit,
            }
        )

    if not lines:
        console.print("[yellow]No items selected — order cancelled.[/yellow]")
        return

    delivery_date = Prompt.ask("Expected delivery date (YYYY-MM-DD)")

    total_value = sum(l["quantity_ordered"] * l["unit_cost"] for l in lines)

    po = PurchaseOrder(
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        status=POStatus.CONFIRMED.value,
        total_value=total_value,
        ordered_at=func.now(),
        expected_delivery=date.fromisoformat(delivery_date),
        created_by=OrderBy.OWNER.value,
    )
    session.add(po)
    session.flush()

    for l in lines:
        session.add(
            POLine(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                inventory_item_id=l["inventory_item_id"],
                supplier_item_id=l["supplier_item_id"],
                quantity_ordered=l["quantity_ordered"],
                unit_cost=l["unit_cost"],
            )
        )

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=None,
            to_status=POStatus.CONFIRMED.value,
            changed_by=OrderBy.OWNER.value,
            note="Manual order created via CLI",
        )
    )

    session.commit()
    console.print(f"[green]✓ Purchase order created. Total: ${total_value:.2f}[/green]")


@app.command()
def receive_standing(supplier_id: str):
    session, tenant = get_tenant()
    console = Console()

    supplier = session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.is_active.is_(True))
    )

    if not supplier:
        console.print("[yellow]No active supplier with given id.[/yellow]")
        return

    if not supplier.delivery_days:
        console.print("[red]This supplier has no standing delivery schedule.[/red]")
        return

    supplier_items = session.execute(
        select(SupplierItem, InventoryItem)
        .join(InventoryItem, SupplierItem.inventory_item_id == InventoryItem.id)
        .where(SupplierItem.supplier_id == supplier.id)
        .where(SupplierItem.tenant_id == tenant.id)
    ).all()

    if not supplier_items:
        console.print("[yellow]No items linked to this supplier.[/yellow]")
        return

    receive_date = Prompt.ask("Delivery date (YYYY-MM-DD)")

    lines = []
    for si, item in supplier_items:
        default = si.default_quantity if si.default_quantity else ""
        while True:
            received = Prompt.ask(
                f"[cyan]{item.name}[/cyan] (expected: {default} {item.unit}) Received quantity [enter to skip / d for default]",
                default=str(default) if default else "",
            )
            if not received:
                break
            if received == "d":
                if not default:
                    console.print(
                        f"[red]No default quantity set for {item.name}.[/red]"
                    )
                    continue
                received = default
            break

        quantity = Decimal(received)
        if quantity <= 0:
            continue

        lines.append(
            {
                "inventory_item_id": item.id,
                "supplier_item_id": si.id,
                "quantity": quantity,
                "unit_cost": si.cost_per_unit,
            }
        )

    if not lines:
        console.print("[yellow]No items received — nothing recorded.[/yellow]")
        return

    total_value = sum(l["quantity"] * l["unit_cost"] for l in lines)

    po = PurchaseOrder(
        tenant_id=tenant.id,
        supplier_id=supplier.id,
        status=POStatus.RECEIVED.value,
        total_value=total_value,
        actual_delivery=date.fromisoformat(receive_date),
        created_by=OrderBy.OWNER.value,
    )
    session.add(po)
    session.flush()

    for l in lines:
        session.add(
            POLine(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                inventory_item_id=l["inventory_item_id"],
                supplier_item_id=l["supplier_item_id"],
                quantity_ordered=l["quantity"],
                quantity_received=l["quantity"],
                unit_cost=l["unit_cost"],
            )
        )

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=None,
            to_status=POStatus.RECEIVED.value,
            changed_by=OrderBy.OWNER.value,
            note="Standing delivery received via CLI",
        )
    )

    session.commit()

    for l in lines:
        publish_event(
            EventCategory.INVENTORY,
            InventoryEventType.ORDER_RECEIVED.value,
            "2",
            {
                "item_id": str(l["inventory_item_id"]),
                "quantity": float(l["quantity"]),
                "transaction_type": InventoryTransactionType.RESTOCK.value,
                "note": f"Standing delivery from {supplier.name}",
            },
            str(tenant.id),
        )

    console.print(
        f"[green]✓ Standing delivery recorded. Total: ${total_value:.2f}[/green]"
    )


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
            changed_by=OrderBy.OWNER.value,
            note="Approved via CLI",
        )
    )

    session.commit()
    publish_event(
        EventCategory.PROCUREMENT,
        ProcurementEventType.PO_APPROVED.value,
        "2",
        {"purchase_order_id": str(po.id), "changed_by": OrderBy.OWNER.value},
        str(tenant.id),
    )
    console.print("[green]✓ Purchase order approved.[/green]")


@app.command()
def confirm(po_id: str):
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

    if po.status != POStatus.SENT.value:
        console.print(
            f"[red]Cannot confirm -- status is '{po.status}', expected 'sent'.[/red]"
        )
        return

    po_lines = session.execute(
        select(POLine, InventoryItem)
        .join(InventoryItem, POLine.inventory_item_id == InventoryItem.id)
        .where(POLine.purchase_order_id == po.id)
        .where(POLine.tenant_id == tenant.id)
    ).all()

    delivery_date = Prompt.ask("Expected delivery date (YYYY-MM-DD)")
    po.expected_delivery = date.fromisoformat(delivery_date)
    po.ordered_at = func.now()

    edits = []
    for line, item in po_lines:
        new_qty = Prompt.ask(
            f"[cyan]{item.name}[/cyan] (ordered: {line.quantity_ordered} {item.unit}) "
            f"New quantity [enter to keep]"
        )
        if new_qty:
            original = line.quantity_ordered
            line.quantity_ordered = Decimal(new_qty)
            edits.append(
                {"item": item.name, "from": float(original), "to": float(new_qty)}
            )

    all_lines = session.scalars(
        select(POLine)
        .where(POLine.purchase_order_id == po.id)
        .where(POLine.tenant_id == tenant.id)
    ).all()
    po.total_value = sum(l.quantity_ordered * l.unit_cost for l in all_lines)

    po.status = POStatus.CONFIRMED.value

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=POStatus.SENT.value,
            to_status=POStatus.CONFIRMED.value,
            changed_by=OrderBy.OWNER.value,
            note=f"Confirmed via CLI. Edits: {edits}" if edits else "Confirmed via CLI",
            edits=edits,
        )
    )

    session.commit()
    publish_event(
        EventCategory.PROCUREMENT,
        ProcurementEventType.PO_CONFIRMED.value,
        "2",
        {"purchase_order_id": str(po.id)},
        str(tenant.id),
    )
    console.print(
        f"[green]✓ Purchase order confirmed. Total: ${po.total_value:.2f}[/green]"
    )


@app.command()
def receive(po_id: str):
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

    if po.status != POStatus.CONFIRMED.value:
        console.print(
            f"[red]Cannot receive -- status is '{po.status}', expected 'confirmed'.[/red]"
        )
        return

    po_lines = session.execute(
        select(POLine, InventoryItem)
        .join(InventoryItem, POLine.inventory_item_id == InventoryItem.id)
        .where(POLine.purchase_order_id == po.id)
        .where(POLine.tenant_id == tenant.id)
    ).all()

    if not po_lines:
        console.print("[yellow]Empty order.[/yellow]")
        return

    receive_date = Prompt.ask("Delivery date (YYYY-MM-DD)")
    po.actual_delivery = date.fromisoformat(receive_date)

    discrepancies = []
    for line, item in po_lines:
        received = Prompt.ask(
            f"[cyan]{item.name}[/cyan] (ordered: {line.quantity_ordered} {item.unit}) Received quantity"
        )
        line.quantity_received = Decimal(received)

        if line.quantity_received != line.quantity_ordered:
            discrepancies.append(
                {
                    "item": item.name,
                    "ordered": float(line.quantity_ordered),
                    "received": float(line.quantity_received),
                }
            )

    po.status = POStatus.RECEIVED.value

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=POStatus.CONFIRMED.value,
            to_status=POStatus.RECEIVED.value,
            changed_by=OrderBy.OWNER.value,
            note=f"Received via CLI. Discrepancies: {discrepancies}"
            if discrepancies
            else "Received via CLI",
        )
    )

    session.commit()

    for line, item in po_lines:
        if line.quantity_received and line.quantity_received > 0:
            publish_event(
                EventCategory.INVENTORY,
                InventoryEventType.ORDER_RECEIVED.value,
                "2",
                {
                    "item_id": str(line.inventory_item_id),
                    "quantity": float(line.quantity_received),
                    "transaction_type": InventoryTransactionType.RESTOCK.value,
                    "note": f"PO {po.id} received",
                },
                str(tenant.id),
            )

    publish_event(
        EventCategory.PROCUREMENT,
        ProcurementEventType.PO_RECEIVED.value,
        "2",
        {"purchase_order_id": str(po.id)},
        str(tenant.id),
    )

    if discrepancies:
        console.print("[yellow]Discrepancies noted:[/yellow]")
        for d in discrepancies:
            console.print(
                f"  {d['item']}: ordered {d['ordered']}, received {d['received']}"
            )

    console.print("[green]✓ Purchase order received.[/green]")


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
            changed_by=OrderBy.OWNER.value,
            note="Rejected via CLI",
        )
    )

    session.commit()
    evaluate_demotion(session, str(tenant.id), str(po.supplier_id))
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
                changed_by=OrderBy.OWNER.value,
                note="Cancelled during edit",
            )
        )
        session.commit()
        evaluate_demotion(session, str(tenant.id), str(po.supplier_id))
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
                changed_by=OrderBy.OWNER.value,
                note="All lines removed during edit",
            )
        )
        session.commit()
        evaluate_demotion(session, str(tenant.id), str(po.supplier_id))
        console.print("[yellow]All lines removed — order cancelled.[/yellow]")
        return

    po.total_value = sum(l.quantity_ordered * l.unit_cost for l in remaining_lines)

    session.add(
        POEvent(
            tenant_id=tenant.id,
            purchase_order_id=po.id,
            from_status=POStatus.PROPOSED.value,
            to_status=POStatus.PROPOSED.value,
            changed_by=OrderBy.OWNER.value,
            note=f"Edited via CLI: {edits}",
            edits=edits,
        )
    )

    po.suggested_topups = None
    session.commit()
    evaluate_demotion(session, str(tenant.id), str(po.supplier_id))
    console.print(
        f"[green]✓ Purchase order updated. New total: ${po.total_value:.2f}[/green]"
    )
