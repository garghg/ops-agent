from sqlalchemy import select
import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from src.cli.context import get_tenant
from src.db.models.inventory import InventoryItem
from src.db.models.counts import PhysicalCount, CountLine
from src.services.shrinkage_service import compute_shrinkage_rates
from decimal import Decimal

app = typer.Typer()
console = Console()


@app.command()
def enter():
    session, tenant = get_tenant()
    
    try:
        counted_by = Prompt.ask("Your name")

        inventory = session.scalars(
            select(InventoryItem).where(InventoryItem.tenant_id == tenant.id)
        ).all()

        if not inventory:
            console.print(f"[red]No inventory found for {tenant.name}[/red]")
            return

        console.print(f"\n[bold]Cycle Count — {tenant.name}[/bold]")
        console.print(f"Counted by: {counted_by}")
        console.print(f"Items to count: {len(inventory)}\n")

        counted_items = []
        for item in inventory:
            raw = Prompt.ask(
                f"  [cyan]{item.name}[/cyan] (system: [yellow]{item.quantity_on_hand} {item.unit}[/yellow]) ['s' to skip]"
            )
            if raw.lower() == "s":
                continue
            actual = Decimal(raw)
            counted_items.append(
                {
                    "item": item,
                    "expected": item.quantity_on_hand,
                    "actual": actual,
                    "discrepancy": actual - item.quantity_on_hand,
                }
            )

        if not counted_items:
            console.print("[yellow]Nothing counted. No changes saved.[/yellow]")
            return

        count = PhysicalCount(tenant_id=tenant.id, counted_by=counted_by)
        session.add(count)
        session.flush()

        for entry in counted_items:
            session.add(
                CountLine(
                    tenant_id=tenant.id,
                    physical_count_id=count.id,
                    inventory_item_id=entry["item"].id,
                    expected_quantity=entry["expected"],
                    actual_quantity=entry["actual"],
                    discrepancy=entry["discrepancy"],
                )
            )
            entry["item"].quantity_on_hand = entry["actual"]

        compute_shrinkage_rates(session, count.id)
        session.commit()

        table = Table(title=f"Count Summary — {len(counted_items)} items")
        table.add_column("Item")
        table.add_column("Expected", justify="right")
        table.add_column("Actual", justify="right")
        table.add_column("Discrepancy", justify="right")

        for entry in counted_items:
            diff = entry["discrepancy"]
            sign = "+" if diff >= 0 else ""
            color = "green" if diff >= 0 else "red"
            table.add_row(
                entry["item"].name,
                f"{entry['expected']} {entry['item'].unit}",
                f"{entry['actual']} {entry['item'].unit}",
                f"[{color}]{sign}{diff}[/{color}]",
            )

        console.print()
        console.print(table)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Count cancelled. No changes saved.[/yellow]")
        session.rollback()
        session.close()