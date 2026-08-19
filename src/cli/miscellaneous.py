from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from src.cli.context import get_tenant
from src.db.models import (
    DecisionLog,
    EmailOutbox,
    Heartbeat,
    InventoryItem,
    PurchaseOrder,
    Supplier,
)

console = Console()


def health():
    session, _ = get_tenant()

    heartbeats = session.scalars(
        select(Heartbeat).order_by(Heartbeat.consumer_name)
    ).all()

    if not heartbeats:
        console.print("[yellow]No heartbeats recorded yet.[/yellow]")
        return

    now = datetime.now(UTC)
    stale_threshold = timedelta(minutes=2)

    table = Table(title="System Health")
    table.add_column("Consumer")
    table.add_column("Last Heartbeat")
    table.add_column("Ago")
    table.add_column("Status")

    for hb in heartbeats:
        ago = now - hb.last_heartbeat.replace(tzinfo=UTC)
        ago_str = f"{int(ago.total_seconds())}s"
        if ago.total_seconds() > 60:
            ago_str = f"{int(ago.total_seconds() / 60)}m"
        if ago.total_seconds() > 3600:
            ago_str = f"{int(ago.total_seconds() / 3600)}h"

        if ago > stale_threshold:
            status = "[red]STALE[/red]"
        else:
            status = "[green]OK[/green]"

        table.add_row(
            hb.consumer_name,
            hb.last_heartbeat.strftime("%Y-%m-%d %H:%M:%S"),
            ago_str,
            status,
        )

    console.print(table)


def outbox():
    session, tenant = get_tenant()

    emails = session.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.tenant_id == tenant.id)
        .order_by(EmailOutbox.created_at.desc())
        .limit(20)
    ).all()

    if not emails:
        console.print("[yellow]No emails in outbox.[/yellow]")
        return

    table = Table(title=f"Email Outbox — {tenant.name}")
    table.add_column("Recipient")
    table.add_column("Subject", max_width=30)
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Created")
    table.add_column("Sent")

    for email in emails:
        if email.status == "sent":
            status = "[green]sent[/green]"
        elif email.status == "pending":
            status = "[yellow]pending[/yellow]"
        else:
            status = "[red]failed[/red]"

        table.add_row(
            email.recipient,
            email.subject,
            status,
            str(email.attempts),
            email.created_at.strftime("%Y-%m-%d %H:%M"),
            email.sent_at.strftime("%Y-%m-%d %H:%M") if email.sent_at else "—",
        )

    console.print(table)


def explain(po_id: str):
    session, tenant = get_tenant()

    po = session.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .where(PurchaseOrder.tenant_id == tenant.id)
    )

    if not po:
        console.print("[red]Purchase order not found.[/red]")
        return

    supplier = session.scalar(select(Supplier).where(Supplier.id == po.supplier_id))

    console.print(f"\n[bold]PO for {supplier.name}[/bold]")
    console.print(f"  Status: {po.status}")
    console.print(f"  Total: ${po.total_value}")
    console.print(f"  Created: {po.created_at.strftime('%Y-%m-%d %H:%M')}")
    console.print(f"  Created by: {po.created_by}")

    decisions = session.scalars(
        select(DecisionLog)
        .where(DecisionLog.purchase_order_id == po.id)
        .where(DecisionLog.tenant_id == tenant.id)
        .order_by(DecisionLog.created_at)
    ).all()

    if not decisions:
        console.print("\n[yellow]No decision log entries for this PO.[/yellow]")
        return

    for d in decisions:
        item = session.scalar(
            select(InventoryItem).where(InventoryItem.id == d.inventory_item_id)
        )
        s = d.snapshot

        console.print(f"\n  [bold]{item.name}[/bold]")
        console.print(f"    Mode: {s.get('mode', '—')}")
        console.print(f"    On hand: {s.get('quantity_on_hand', '—')}")
        console.print(f"    On order: {s.get('on_order', '—')}")
        console.print(f"    Position: {s.get('position', '—')}")

        horizon = s.get("protection_horizon")
        if horizon:
            console.print(f"    Protection horizon: {horizon['start']} → {horizon['end']}")

        if s.get("aggregate_demand") is not None:
            console.print(f"    Forecast demand: {s['aggregate_demand']:.1f}")

        if s.get("quantile_key"):
            console.print(f"    Quantile key: {s['quantile_key']}")

        if s.get("quantile_grid"):
            grid = s["quantile_grid"]
            grid_str = ", ".join(f"{k}={v:.1f}" for k, v in grid.items())
            console.print(f"    Quantile grid: {grid_str}")

        if s.get("target_quantity") is not None:
            console.print(f"    Target (par): {s['target_quantity']}")

        if s.get("shrinkage_factor") is not None:
            console.print(f"    Shrinkage factor: {s['shrinkage_factor']:.4f}")

        console.print(f"    Shortfall: {s.get('shortfall', '—')}")
        console.print(f"    Pack size: {s.get('pack_size', '—')}")
        console.print(f"    Quantity ordered: {s.get('quantity_ordered', '—')}")
        console.print(f"    Unit cost: ${s.get('unit_cost', '—')}")

        if s.get("shelf_life_cap") is not None:
            console.print(f"    Shelf life cap: {s['shelf_life_cap']}")