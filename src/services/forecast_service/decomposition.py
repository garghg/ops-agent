from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import (
    DailyActual,
    Forecast,
    IntradayProfile,
    InventoryTransaction,
    ItemDemandForecast,
    SaleLineItem,
    SaleTransaction,
    ShareVector,
    Tenant,
)
from src.schemas.forecast import ForecastSeries
from src.schemas.inventory import InventoryTransactionType
from src.schemas.models import ModelVersion
from src.schemas.sale import SaleTransactionType
from src.services.forecast_service.config import (
    FORECAST_HORIZON,
    INTRADAY_LOOKBACK_DAYS,
    SEASONAL_NAIVE_LOOKBACK_DAYS,
)


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
        .where(DailyActual.series == ForecastSeries.TOTAL_UNITS)
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
        item_map[(item_id, sale_date)] = abs(sale.quantity_change) + item_map.get(
            (item_id, sale_date), 0
        )

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


def compute_item_demand(session: Session, tenant_id: str, as_of_date: str):
    as_of_date = date.fromisoformat(as_of_date)

    forecasts = session.scalars(
        select(Forecast)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.target_date <= as_of_date + timedelta(days=FORECAST_HORIZON))
        .where(Forecast.target_date > as_of_date)
        .where(Forecast.model_version == ModelVersion.POISSON_GLM.value)
        .where(Forecast.forecast_date == as_of_date)
        .where(Forecast.series == ForecastSeries.TOTAL_UNITS)
    ).all()

    latest_share_date = session.scalar(
        select(func.max(ShareVector.as_of_date))
        .where(ShareVector.tenant_id == tenant_id)
        .where(ShareVector.as_of_date <= as_of_date)
    )

    if not latest_share_date:
        return

    share_vectors = session.scalars(
        select(ShareVector)
        .where(ShareVector.tenant_id == tenant_id)
        .where(ShareVector.as_of_date == latest_share_date)
    ).all()

    if not forecasts:
        forecasts = session.scalars(
            select(Forecast)
            .where(Forecast.tenant_id == tenant_id)
            .where(
                Forecast.target_date <= as_of_date + timedelta(days=FORECAST_HORIZON)
            )
            .where(Forecast.target_date > as_of_date)
            .where(Forecast.model_version == ModelVersion.SEASONAL_NAIVE.value)
            .where(Forecast.forecast_date == as_of_date)
            .where(Forecast.series == ForecastSeries.TOTAL_UNITS)
        ).all()

    if not forecasts or not share_vectors:
        return

    sv_map = {(sv.inventory_item_id, sv.day_of_week): sv.share for sv in share_vectors}

    for forecast in forecasts:
        day = forecast.target_date.weekday()
        for k, v in sv_map.items():
            if k[1] == day:
                point_estimate = forecast.point_estimate * v
                quantile_grid = None
                if forecast.quantile_grid:
                    quantile_grid = {
                        gk: float(float(v) * gv) for gk, gv in forecast.quantile_grid.items()
                    }

                stmt = insert(ItemDemandForecast).values(
                    tenant_id=tenant_id,
                    inventory_item_id=k[0],
                    target_date=forecast.target_date,
                    point_estimate=point_estimate,
                    quantile_grid=quantile_grid,
                    model_version=forecast.model_version,
                    as_of_date=as_of_date,
                )
                stmt = stmt.on_conflict_do_update(
                    constraint="item_demand_forecasts_tenant_item_target_key",
                    set_={
                        "point_estimate": stmt.excluded.point_estimate,
                        "quantile_grid": stmt.excluded.quantile_grid,
                        "model_version": stmt.excluded.model_version,
                        "as_of_date": stmt.excluded.as_of_date,
                    },
                )
                session.execute(stmt)

    session.commit()


def compute_intraday_profiles(session: Session, tenant_id: str, as_of_date: str):
    as_of_date = date.fromisoformat(as_of_date)

    timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))

    tz = ZoneInfo(timezone)

    sales = session.execute(
        select(SaleLineItem, SaleTransaction)
        .join(SaleTransaction, SaleTransaction.id == SaleLineItem.sale_transaction_id)
        .where(SaleLineItem.tenant_id == tenant_id)
        .where(SaleTransaction.transaction_type == SaleTransactionType.SALE.value)
        .where(SaleTransaction.timestamp < as_of_date)
        .where(
            SaleTransaction.timestamp
            >= as_of_date - timedelta(days=INTRADAY_LOOKBACK_DAYS)
        )
    ).all()

    if not sales or not tz:
        return
    
    sales_map = {}
    day_totals = {}
    for item, transaction in sales:
        local_time = transaction.timestamp.astimezone(tz)
        local_hour = local_time.hour
        local_date = local_time.date()
        day_totals[local_date] = item.quantity + day_totals.get(local_date, 0)
        sales_map[(local_date, local_hour)] = item.quantity + sales_map.get(
            (local_date, local_hour), 0
        )
    
    sales_per_hour = defaultdict(list)
    for k, v in sales_map.items():
        day = k[0].weekday()
        total = day_totals.get(k[0])
        if total == 0 or not total:
            continue
        rate = v / total
        sales_per_hour[(day, k[1])].append(rate)
        
    for dt, rates in sales_per_hour.items():
        avg = mean(rates)
        stmt = insert(IntradayProfile).values(
            tenant_id=tenant_id,
            day_of_week=dt[0],
            hour=dt[1],
            fraction=avg,
            as_of_date=as_of_date,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="intraday_profiles_tenant_dow_hour_date_key",
            set_={"fraction": stmt.excluded.fraction},
        )
        session.execute(stmt)
    
    session.commit()