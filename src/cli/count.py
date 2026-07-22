from sqlalchemy import select
import typer
from src.db.models.core import Tenant
from src.db.models.inventory import InventoryItem
from src.db.models.counts import PhysicalCount, CountLine
from src.db.session import SessionLocal
from decimal import Decimal

app = typer.Typer()


@app.command()
def enter(
    counted_by: str = typer.Option(..., prompt="Your name"),
    tenant_name: str = typer.Option(..., prompt="Tenant name"),
    tenant_address: str = typer.Option(..., prompt="Tenant address"),
    tenant_city: str = typer.Option(..., prompt="Tenant city"),
):
    with SessionLocal() as session:
        match = session.scalar(
            select(Tenant).where(
                Tenant.name.ilike(f"%{tenant_name}%"),
                Tenant.address.ilike(f"%{tenant_address}%"),
                Tenant.location.ilike(f"%{tenant_city}%"),
            )
        )
        if not match:
            print(f"No tenant found matching '{tenant_name} at {tenant_address}'")
            return

        inventory = session.scalars(
            select(InventoryItem).where(InventoryItem.tenant_id == match.id)
        ).all()

        if not inventory:
            print(f"No inventory found for '{tenant_name} at {tenant_address}'")
            return

        counted_items = []
        for item in inventory:
            raw = typer.prompt(
                f"{item.name} (system: {item.quantity_on_hand} {item.unit}) ['s' to skip]"
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
            print("Nothing counted. No changes saved.")
            return

        count = PhysicalCount(tenant_id=match.id, counted_by=counted_by)
        session.add(count)
        session.flush()

        for entry in counted_items:
            session.add(
                CountLine(
                    tenant_id=match.id,
                    physical_count_id=count.id,
                    inventory_item_id=entry["item"].id,
                    expected_quantity=entry["expected"],
                    actual_quantity=entry["actual"],
                    discrepancy=entry["discrepancy"],
                )
            )
            entry["item"].quantity_on_hand = entry["actual"]

        session.commit()

        print(f"\nCount saved. {len(counted_items)} items updated.")
        for entry in counted_items:
            diff = entry["discrepancy"]
            sign = "+" if diff >= 0 else ""
            print(
                f"  {entry['item'].name}: {entry['expected']} → {entry['actual']} ({sign}{diff})"
            )
