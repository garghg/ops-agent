from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Category,
    CountLine,
    InventoryItem,
    InventoryTransaction,
    PhysicalCount,
)
from src.schemas.inventory import SUBTRACT_TYPES
from src.schemas.learning import FactorKind
from src.services.config_services import resolve_config
from src.services.learning_service import update_factor


def compute_shrinkage_rates(session: Session, physical_count_id, tenant_id):
    discrepancy_stmt = (
        select(
            Category.id.label("category_id"),
            Category.name.label("category"),
            func.sum(CountLine.discrepancy).label("total_discrepancy"),
        )
        .select_from(CountLine)
        .join(InventoryItem, CountLine.inventory_item_id == InventoryItem.id)
        .join(Category, InventoryItem.category_id == Category.id)
        .where(CountLine.physical_count_id == physical_count_id)
        .where(CountLine.discrepancy < 0)
        .where(InventoryItem.tenant_id == tenant_id)
        .group_by(Category.id, Category.name)
    )

    discrepancies = session.execute(discrepancy_stmt).all()

    current_count = session.scalar(
        select(PhysicalCount)
        .where(PhysicalCount.id == physical_count_id)
        .where(PhysicalCount.tenant_id == tenant_id)
    )

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
            Category.id.label("category_id"),
            Category.name.label("category"),
            func.sum(InventoryTransaction.quantity_change).label("total_depletion"),
        )
        .select_from(InventoryItem)
        .join(InventoryTransaction, InventoryTransaction.item_id == InventoryItem.id)
        .join(Category, InventoryItem.category_id == Category.id)
        .where(InventoryItem.tenant_id == current_count.tenant_id)
        .where(InventoryTransaction.created_at >= window_start)
        .where(InventoryTransaction.created_at <= current_count.counted_at)
        .where(InventoryTransaction.transaction_type.in_(SUBTRACT_TYPES))
        .group_by(Category.id, Category.name)
    )

    depletions = session.execute(depletion_stmt).all()

    depletion_map = {row.category_id: abs(row.total_depletion) for row in depletions}

    config = resolve_config(str(current_count.tenant_id), session)

    for row in discrepancies:
        depleted = depletion_map.get(row.category_id)

        if not depleted:
            continue

        observation = float(abs(row.total_discrepancy) / depleted)

        update_factor(
            session,
            str(current_count.tenant_id),
            FactorKind.SHRINKAGE,
            str(row.category_id),
            observation,
            config.learning.shrinkage_half_life,
            config.learning.shrinkage_clamp_low,
            config.learning.shrinkage_clamp_high,
            current_count.counted_at.date(),
            default_value=0.0,
        )
