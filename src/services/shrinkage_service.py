from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.schemas.inventory import SUBTRACT_TYPES
from src.db.models.counts import PhysicalCount, CountLine
from src.db.models.inventory import InventoryItem, InventoryTransaction
from src.db.models.shrinkage import ShrinkageRate


def compute_shrinkage_rates(session: Session, physical_count_id):
    discrepancy_stmt = (
        select(
            InventoryItem.category,
            func.sum(CountLine.discrepancy).label("total_discrepancy"),
        )
        .join(InventoryItem, CountLine.inventory_item_id == InventoryItem.id)
        .where(CountLine.physical_count_id == physical_count_id)
        .where(CountLine.discrepancy < 0)
        .group_by(InventoryItem.category)
    )

    discrepancies = session.execute(discrepancy_stmt).all()

    current_count = session.get(PhysicalCount, physical_count_id)

    prev_stmt = (
        select(PhysicalCount)
        .where(PhysicalCount.tenant_id == current_count.tenant_id)
        .where(PhysicalCount.counted_at < current_count.counted_at)
        .order_by(PhysicalCount.counted_at.desc())
        .limit(1)
    )

    prev_count = session.scalar(prev_stmt)

    if prev_count is None:
        return

    window_start = prev_count.counted_at

    depletion_stmt = (
        select(
            InventoryItem.category,
            func.sum(InventoryTransaction.quantity_change).label("total_depletion"),
        )
        .join(InventoryTransaction, InventoryTransaction.item_id == InventoryItem.id)
        .where(InventoryItem.tenant_id == current_count.tenant_id)
        .where(InventoryTransaction.created_at >= window_start)
        .where(InventoryTransaction.created_at <= current_count.counted_at)
        .where(InventoryTransaction.transaction_type.in_(SUBTRACT_TYPES))
        .group_by(InventoryItem.category)
    )

    depletions = session.execute(depletion_stmt).all()

    depletion_map = {row.category: abs(row.total_depletion) for row in depletions}

    for row in discrepancies:
        depleted = depletion_map.get(row.category)

        if not depleted:
            continue

        new_observation = abs(row.total_discrepancy) / depleted

        existing = session.scalar(
            select(ShrinkageRate)
            .where(ShrinkageRate.tenant_id == current_count.tenant_id)
            .where(ShrinkageRate.category == row.category)
        )

        if existing:
            updated_rate = (existing.rate * existing.sample_count + new_observation) / (
                existing.sample_count + 1
            )
            existing.rate = updated_rate
            existing.sample_count += 1
            existing.last_updated = current_count.counted_at
        else:
            session.add(
                ShrinkageRate(
                    tenant_id=current_count.tenant_id,
                    category=row.category,
                    rate=new_observation,
                    sample_count=1,
                    last_updated=current_count.counted_at,
                )
            )

    session.flush()
