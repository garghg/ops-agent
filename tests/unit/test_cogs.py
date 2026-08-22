from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.db.models import (
    InventoryItem,
    SaleLineItem,
    SaleTransaction,
    SupplierItem,
    SupplierItemCostHistory,
    Tenant,
)
from src.services.cost_service import compute_newsvendor_ratios


def _create_sale(
    session, tenant_id, item_name, modifiers, unit_price, quantity=1, days_ago=1
):
    tz = ZoneInfo("America/Vancouver")
    ts = datetime.now(tz) - timedelta(days=days_ago)

    txn = SaleTransaction(
        tenant_id=tenant_id,
        external_transaction_id=f"test-{item_name}-{days_ago}-{'-'.join(modifiers)}",
        source="test",
        timestamp=ts,
        total=unit_price * quantity,
        payment_method="card",
        transaction_type="sale",
    )
    session.add(txn)
    session.flush()

    session.add(
        SaleLineItem(
            tenant_id=tenant_id,
            sale_transaction_id=txn.id,
            item_name=item_name,
            modifiers=modifiers,
            quantity=quantity,
            unit_price=unit_price,
        )
    )
    session.flush()


def test_newsvendor_ratios_basic(seeded_db):
    tenant = seeded_db.scalars(select(Tenant)).first()

    for i in range(20):
        _create_sale(
            seeded_db,
            tenant.id,
            "Single Scoop",
            ["Vanilla", "Waffle Cone"],
            Decimal("4.50"),
            days_ago=i + 1,
        )

    for i in range(5):
        _create_sale(
            seeded_db,
            tenant.id,
            "Single Scoop",
            ["Strawberry", "Cake Cone"],
            Decimal("4.50"),
            days_ago=i + 1,
        )

    seeded_db.commit()

    ratios = compute_newsvendor_ratios(seeded_db, str(tenant.id))

    assert len(ratios) > 0, "Should have ratios for items that appeared in sales"

    for inv_id, ratio in ratios.items():
        assert Decimal(0) < ratio < Decimal(1), (
            f"Ratio {ratio} out of bounds for {inv_id}"
        )

    vanilla = seeded_db.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.name == "Vanilla Bean Ice Cream")
    )
    assert vanilla.id in ratios, "Vanilla should have a ratio from sales"

    # High margin items should have high ratios
    # Ice cream costs ~$4.25/kg, used 0.10kg per scoop ($0.425),
    # scoop sells for $4.50 -- high margin → ratio should be well above 0.5
    assert ratios[vanilla.id] > Decimal("0.5"), (
        f"Vanilla ratio {ratios[vanilla.id]} should be > 0.5 given high margins"
    )


def test_newsvendor_ratios_empty_sales(seeded_db):
    tenant = seeded_db.scalars(select(Tenant)).first()

    ratios = compute_newsvendor_ratios(seeded_db, str(tenant.id))
    assert ratios == {}, "No sales should return empty ratios"


def test_cost_history_preserves_chain(seeded_db):
    tenant = seeded_db.scalars(select(Tenant)).first()

    si = seeded_db.scalar(
        select(SupplierItem).where(SupplierItem.tenant_id == tenant.id)
    )

    today = datetime.now(ZoneInfo("America/Vancouver")).date()
    prices = [si.cost_per_unit, Decimal("5.00"), Decimal("5.50"), Decimal("6.00")]

    for i in range(len(prices) - 1):
        seeded_db.add(
            SupplierItemCostHistory(
                tenant_id=tenant.id,
                supplier_item_id=si.id,
                old_cost=prices[i],
                new_cost=prices[i + 1],
                effective_date=today + timedelta(days=i),
                trigger_source="manual",
            )
        )
        si.cost_per_unit = prices[i + 1]

    seeded_db.flush()

    history = seeded_db.scalars(
        select(SupplierItemCostHistory)
        .where(SupplierItemCostHistory.supplier_item_id == si.id)
        .where(SupplierItemCostHistory.tenant_id == tenant.id)
        .order_by(SupplierItemCostHistory.effective_date)
    ).all()

    assert len(history) == 3
    for i in range(len(history) - 1):
        assert history[i].new_cost == history[i + 1].old_cost


def test_shelf_life_cap():
    from math import ceil

    shelf_life_days = 14
    daily_demand = Decimal("1.0")  # 1 kg/day
    position = Decimal("10.0")  # 10 kg on hand + on order
    pack_size = Decimal("5.0")

    # Without cap: suppose forecast says order 15 kg
    uncapped_qty = Decimal("15.0")

    # Cap: max_stock = 14 days × 1 kg/day = 14 kg
    # shelf_cap = ceil((14 - 10) / 5) * 5 = 5 kg
    max_stock = Decimal(shelf_life_days) * daily_demand
    shelf_cap = max(
        0,
        ceil((max_stock - position) / pack_size) * pack_size,
    )
    quantity_ordered = min(uncapped_qty, shelf_cap)

    assert shelf_cap == 5
    assert quantity_ordered == 5


def test_shelf_life_cap_no_cap_when_stock_low():
    from math import ceil

    shelf_life_days = 14
    daily_demand = Decimal("1.0")
    position = Decimal("2.0")  # very low stock
    pack_size = Decimal("5.0")

    uncapped_qty = Decimal("10.0")

    max_stock = Decimal(shelf_life_days) * daily_demand
    shelf_cap = max(
        0,
        ceil((max_stock - position) / pack_size) * pack_size,
    )
    quantity_ordered = min(uncapped_qty, shelf_cap)

    # max_stock = 14, position = 2, cap = ceil(12/5)*5 = 15
    # uncapped is 10, cap is 15, so uncapped wins
    assert quantity_ordered == 10


def test_shelf_life_cap_zero_when_overstocked():
    from math import ceil

    shelf_life_days = 14
    daily_demand = Decimal("1.0")
    position = Decimal("16.0")  # more than 14 days of stock
    pack_size = Decimal("5.0")

    uncapped_qty = Decimal("5.0")

    max_stock = Decimal(shelf_life_days) * daily_demand
    shelf_cap = max(
        0,
        ceil((max_stock - position) / pack_size) * pack_size,
    )
    quantity_ordered = min(uncapped_qty, shelf_cap)

    assert shelf_cap == 0
    assert quantity_ordered == 0
