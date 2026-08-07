from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import PurchaseOrder, SpendLedger, Supplier
from src.schemas.autonomy import AutonomyState
from src.schemas.suppliers import POStatus
from src.services.config_services import resolve_config


def autonomy_checks(
    session: Session,
    timezone: str,
    tenant_id: str,
    po: PurchaseOrder,
    supplier: Supplier,
    state: str,
) -> bool:
    config = resolve_config()
    today = datetime.now(ZoneInfo(timezone)).date()
    this_monday = today - timedelta(days=today.weekday())

    daily_spend = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.amount), Decimal(0)))
        .where(SpendLedger.tenant_id == tenant_id)
        .where(SpendLedger.business_date == today)
    ) or Decimal(0)

    weekly_spend = session.scalar(
        select(func.coalesce(func.sum(SpendLedger.amount), Decimal(0)))
        .where(SpendLedger.tenant_id == tenant_id)
        .where(SpendLedger.business_date >= this_monday)
    ) or Decimal(0)

    avg_order_value = session.scalar(
        select(func.avg(PurchaseOrder.total_value))
        .where(PurchaseOrder.tenant_id == tenant_id)
        .where(PurchaseOrder.supplier_id == supplier.id)
        .where(
            PurchaseOrder.status.in_(
                [POStatus.SENT, POStatus.CONFIRMED, POStatus.RECEIVED]
            )
        )
    )

    valid_order = po.total_value <= config.ordering.max_order_value
    valid_daily = (daily_spend + po.total_value) <= config.ordering.max_daily_spend
    valid_weekly = (weekly_spend + po.total_value) <= config.ordering.max_weekly_spend
    valid_novelty = (
        avg_order_value is not None
        and po.total_value
        <= avg_order_value * Decimal(str(config.ordering.novelty_threshold))
    )
    valid_minimum = (
        not supplier.minimum_order_value
        or po.total_value >= supplier.minimum_order_value
    )
    valid_state = False
    if state and state == AutonomyState.AUTO_WITHIN_BOUNDS.value:
        valid_state = True

    all_passed = all(
        [valid_order, valid_daily, valid_weekly, valid_novelty, valid_minimum, valid_state]
    )

    return all_passed