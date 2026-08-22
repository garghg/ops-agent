from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Category,
    CountLine,
    InventoryItem,
    InventoryTransaction,
    PhysicalCount,
    Tenant,
)
from src.events.bus import publish_event
from src.schemas.anomaly import AnomalyType
from src.schemas.event import EventCategory, InventoryEventType
from src.schemas.inventory import (
    SUBTRACT_TYPES,
    InventoryEventPayload,
    InventoryTransactionType,
)
from src.schemas.learning import FactorKind
from src.services.anomaly_service import persist_anomaly
from src.services.config_services import resolve_config
from src.services.learning_service import get_factor, update_factor


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
        .where(InventoryTransaction.occurred_at >= window_start)
        .where(InventoryTransaction.occurred_at <= current_count.counted_at)
        .where(InventoryTransaction.transaction_type.in_(SUBTRACT_TYPES))
        .where(
            InventoryTransaction.transaction_type
            != InventoryTransactionType.SHRINKAGE.value
        )
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

        current_factor = get_factor(
            session,
            str(current_count.tenant_id),
            FactorKind.SHRINKAGE,
            str(row.category_id),
            default=0.0,
        )

        if current_factor > 0 and observation > 3 * float(current_factor):
            persist_anomaly(
                session,
                str(current_count.tenant_id),
                AnomalyType.COUNT_DISCREPANCY,
                f"category:{row.category}",
                1,
                current_count.counted_at.date(),
                {
                    "category": row.category,
                    "observation": observation,
                    "current_factor": float(current_factor),
                    "ratio": round(observation / float(current_factor), 1),
                    "total_discrepancy": float(abs(row.total_discrepancy)),
                    "total_depletion": float(depleted),
                },
                f"Count discrepancy in {row.category} is {observation / float(current_factor):.1f}× the learned shrinkage rate "
                f"({observation:.1%} vs {float(current_factor):.1%}). Possible spoilage event, theft, or miscount.",
                config.anomalies.cooldown_hours,
            )

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


def apply_daily_shrinkage(session: Session, tenant_id: str, business_date: str):
    business_date = date.fromisoformat(business_date)

    tz = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    day_start = datetime.combine(business_date, time.min, tzinfo=ZoneInfo(tz))
    day_end = datetime.combine(
        business_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(tz)
    )

    sub_by_item = session.execute(
        select(
            InventoryTransaction.item_id,
            func.sum(func.abs(InventoryTransaction.quantity_change)).label(
                "total_usage"
            ),
        )
        .where(InventoryTransaction.tenant_id == tenant_id)
        .where(InventoryTransaction.transaction_type.in_(SUBTRACT_TYPES))
        .where(
            InventoryTransaction.transaction_type
            != InventoryTransactionType.SHRINKAGE.value
        )
        .where(InventoryTransaction.occurred_at >= day_start)
        .where(InventoryTransaction.occurred_at < day_end)
        .group_by(InventoryTransaction.item_id)
    ).all()

    if not sub_by_item:
        return

    item_ids = [row.item_id for row in sub_by_item]
    items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.id.in_(item_ids))
        .where(InventoryItem.tenant_id == tenant_id)
    ).all()

    category_map = {item.id: str(item.category_id) for item in items}

    for row in sub_by_item:
        category_id = category_map.get(row.item_id)
        if not category_id:
            continue

        factor = get_factor(
            session, tenant_id, FactorKind.SHRINKAGE, category_id, default=0.0
        )

        if factor <= 0:
            continue

        shrinkage_qty = float(row.total_usage) * float(factor)

        item = next((i for i in items if i.id == row.item_id), None)
        if item and shrinkage_qty > float(item.quantity_on_hand):
            shrinkage_qty = max(0.0, float(item.quantity_on_hand))

        if shrinkage_qty <= 0:
            continue

        publish_event(
            EventCategory.INVENTORY,
            InventoryEventType.SHRINKAGE_DEPLETION.value,
            "3",
            InventoryEventPayload(
                item_id=row.item_id,
                quantity=shrinkage_qty,
                transaction_type=InventoryTransactionType.SHRINKAGE,
                note=f"Daily shrinkage: {business_date}",
                source_key=f"shrinkage:{business_date}:{row.item_id}",
            ).model_dump(mode="json"),
            str(tenant_id),
        )
