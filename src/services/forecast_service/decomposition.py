from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import DailyActual, InventoryTransaction, ShareVector
from src.schemas.inventory import InventoryTransactionType
from src.services.forecast_service.config import SEASONAL_NAIVE_LOOKBACK_DAYS


def compute_share_vectors(session: Session, tenant_id: str, as_of_date: str):
    as_of_date = date.fromisoformat(as_of_date)
    sales = session.scalars(
        select(InventoryTransaction)
        .where(
            InventoryTransaction.transaction_type
            == InventoryTransactionType.USAGE.value
        )
        .where(InventoryTransaction.occurred_at < as_of_date)
        .where(
            InventoryTransaction.occurred_at
            >= as_of_date - timedelta(days=SEASONAL_NAIVE_LOOKBACK_DAYS)
        )
        .where(InventoryTransaction.tenant_id == tenant_id)
    ).all()

    actuals = session.scalars(
        select(DailyActual)
        .where(DailyActual.series == "total_units")
        .where(DailyActual.actual_date < as_of_date)
        .where(
            DailyActual.actual_date
            >= as_of_date - timedelta(days=SEASONAL_NAIVE_LOOKBACK_DAYS)
        )
        .where(DailyActual.tenant_id == tenant_id)
    ).all()

    if not sales or not actuals:
        return

    item_map = {}
    for sale in sales:
        item_id = sale.item_id
        sale_date = sale.occurred_at.date()
        item_map[(item_id, sale_date)] = abs(
            sale.quantity_change
        ) + item_map.get((item_id, sale_date), 0)
        
    actuals_date_map = {actual.actual_date: actual for actual in actuals}

    shares = defaultdict(list)
    for k, v in item_map.items():
        actual = actuals_date_map.get(k[1])
        if not actual or actual.value == 0:
            continue
        share = v / actual.value
        shares[(k[0], k[1].weekday())].append(share)
    
    for key, lst in shares.items():
        m = mean(lst)
        stmt = insert(ShareVector).values(
            tenant_id=tenant_id,
            inventory_item_id=key[0],
            day_of_week=key[1],
            share=m,
            as_of_date=as_of_date,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="share_vectors_tenant_item_dow_date_key",
            set_={"share": stmt.excluded.share},
        )
        session.execute(stmt)
    
    session.commit()