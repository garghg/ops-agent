from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from src.db.models import (
    DailyActual,
    Forecast,
    IntradayProfile,
    InventoryItem,
    InventoryTransaction,
    ItemDemandForecast,
    SaleLineItem,
    SaleTransaction,
    ShareVector,
    Tenant,
)
from src.services.forecast_service import (
    compute_intraday_profiles,
    compute_item_demand,
    compute_share_vectors,
)


@pytest.fixture
def tenant(seeded_db):
    t = seeded_db.scalar(select(Tenant).limit(1))
    t.timezone = "America/Vancouver"
    seeded_db.commit()
    return t


@pytest.fixture
def inventory_items(seeded_db, tenant):
    items = [
        InventoryItem(
            tenant_id=tenant.id,
            name="Chocolate Base",
            quantity_on_hand=Decimal("100.00"),
            reorder_point=Decimal("20.00"),
            target_quantity=Decimal("80.00"),
            cost_per_unit=Decimal("2.00"),
            unit="oz",
            category="base",
        ),
        InventoryItem(
            tenant_id=tenant.id,
            name="Vanilla Base",
            quantity_on_hand=Decimal("100.00"),
            reorder_point=Decimal("20.00"),
            target_quantity=Decimal("80.00"),
            cost_per_unit=Decimal("2.00"),
            unit="oz",
            category="base",
        ),
    ]
    seeded_db.add_all(items)
    seeded_db.commit()
    return items


class TestComputeShareVectors:
    def test_computes_shares_per_item_per_weekday(
        self, seeded_db, tenant, inventory_items
    ):
        choc, vanilla = inventory_items
        # Seed 4 Mondays (weekday 0) of usage + actuals
        mondays = [
            date(2026, 6, 29),
            date(2026, 7, 6),
            date(2026, 7, 13),
            date(2026, 7, 20),
        ]
        for d in mondays:
            # Chocolate: 60 units used, Vanilla: 40 units used, Total: 100
            seeded_db.add(
                InventoryTransaction(
                    tenant_id=tenant.id,
                    item_id=choc.id,
                    quantity_change=Decimal("-60.00"),
                    transaction_type="usage",
                    occurred_at=datetime.combine(d, datetime.min.time()).replace(
                        tzinfo=None
                    ),
                    event_id=f"choc-{d}",
                )
            )
            seeded_db.add(
                InventoryTransaction(
                    tenant_id=tenant.id,
                    item_id=vanilla.id,
                    quantity_change=Decimal("-40.00"),
                    transaction_type="usage",
                    occurred_at=datetime.combine(d, datetime.min.time()).replace(
                        tzinfo=None
                    ),
                    event_id=f"vanilla-{d}",
                )
            )
            seeded_db.add(
                DailyActual(
                    tenant_id=tenant.id,
                    series="total_units",
                    actual_date=d,
                    value=Decimal("100.00"),
                )
            )
        seeded_db.commit()

        compute_share_vectors(seeded_db, str(tenant.id), "2026-07-21")

        choc_share = seeded_db.scalar(
            select(ShareVector.share).where(
                ShareVector.tenant_id == tenant.id,
                ShareVector.inventory_item_id == choc.id,
                ShareVector.day_of_week == 0,
            )
        )
        vanilla_share = seeded_db.scalar(
            select(ShareVector.share).where(
                ShareVector.tenant_id == tenant.id,
                ShareVector.inventory_item_id == vanilla.id,
                ShareVector.day_of_week == 0,
            )
        )
        assert float(choc_share) == pytest.approx(0.6)
        assert float(vanilla_share) == pytest.approx(0.4)

    def test_skips_zero_actual_days(self, seeded_db, tenant, inventory_items):
        choc = inventory_items[0]
        d = date(2026, 7, 20)
        seeded_db.add(
            InventoryTransaction(
                tenant_id=tenant.id,
                item_id=choc.id,
                quantity_change=Decimal("-10.00"),
                transaction_type="usage",
                occurred_at=datetime.combine(d, datetime.min.time()).replace(
                    tzinfo=None
                ),
                event_id=f"choc-zero-{d}",
            )
        )
        seeded_db.add(
            DailyActual(
                tenant_id=tenant.id,
                series="total_units",
                actual_date=d,
                value=Decimal("0.00"),
            )
        )
        seeded_db.commit()

        compute_share_vectors(seeded_db, str(tenant.id), "2026-07-21")

        shares = seeded_db.scalars(
            select(ShareVector).where(ShareVector.tenant_id == tenant.id)
        ).all()
        assert len(shares) == 0

    def test_returns_none_with_no_data(self, seeded_db, tenant):
        result = compute_share_vectors(seeded_db, str(tenant.id), "2026-07-21")
        assert result is None


class TestComputeItemDemand:
    def test_multiplies_forecast_by_share(self, seeded_db, tenant, inventory_items):
        choc, vanilla = inventory_items
        as_of = date(2026, 7, 20)
        # Monday July 21 target (weekday 0)
        target = date(2026, 7, 21)
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_units",
                target_date=target,
                forecast_date=as_of,
                model_version="poisson_glm",
                point_estimate=Decimal("100.00"),
            )
        )
        seeded_db.add(
            ShareVector(
                tenant_id=tenant.id,
                inventory_item_id=choc.id,
                day_of_week=1,
                share=Decimal("0.600000"),
                as_of_date=as_of,
            )
        )
        seeded_db.add(
            ShareVector(
                tenant_id=tenant.id,
                inventory_item_id=vanilla.id,
                day_of_week=1,
                share=Decimal("0.400000"),
                as_of_date=as_of,
            )
        )
        seeded_db.commit()

        compute_item_demand(seeded_db, str(tenant.id), "2026-07-20")

        choc_demand = seeded_db.scalar(
            select(ItemDemandForecast.point_estimate).where(
                ItemDemandForecast.tenant_id == tenant.id,
                ItemDemandForecast.inventory_item_id == choc.id,
                ItemDemandForecast.target_date == target,
            )
        )
        vanilla_demand = seeded_db.scalar(
            select(ItemDemandForecast.point_estimate).where(
                ItemDemandForecast.tenant_id == tenant.id,
                ItemDemandForecast.inventory_item_id == vanilla.id,
                ItemDemandForecast.target_date == target,
            )
        )
        assert float(choc_demand) == pytest.approx(60.0)
        assert float(vanilla_demand) == pytest.approx(40.0)

    def test_scales_quantile_grid(self, seeded_db, tenant, inventory_items):
        choc = inventory_items[0]
        as_of = date(2026, 7, 20)
        target = date(2026, 7, 21)
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_units",
                target_date=target,
                forecast_date=as_of,
                model_version="poisson_glm",
                point_estimate=Decimal("100.00"),
                quantile_grid={"p05": 80, "p95": 120},
            )
        )
        seeded_db.add(
            ShareVector(
                tenant_id=tenant.id,
                inventory_item_id=choc.id,
                day_of_week=1,
                share=Decimal("0.500000"),
                as_of_date=as_of,
            )
        )
        seeded_db.commit()

        compute_item_demand(seeded_db, str(tenant.id), "2026-07-20")

        demand = seeded_db.scalar(
            select(ItemDemandForecast).where(
                ItemDemandForecast.tenant_id == tenant.id,
                ItemDemandForecast.inventory_item_id == choc.id,
                ItemDemandForecast.target_date == target,
            )
        )
        assert demand.quantile_grid["p05"] == pytest.approx(40.0)
        assert demand.quantile_grid["p95"] == pytest.approx(60.0)

    def test_falls_back_to_naive(self, seeded_db, tenant, inventory_items):
        choc = inventory_items[0]
        as_of = date(2026, 7, 20)
        target = date(2026, 7, 21)
        # No GLM forecast, only naive
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_units",
                target_date=target,
                forecast_date=as_of,
                model_version="seasonal_naive",
                point_estimate=Decimal("80.00"),
            )
        )
        seeded_db.add(
            ShareVector(
                tenant_id=tenant.id,
                inventory_item_id=choc.id,
                day_of_week=1,
                share=Decimal("0.500000"),
                as_of_date=as_of,
            )
        )
        seeded_db.commit()

        compute_item_demand(seeded_db, str(tenant.id), "2026-07-20")

        demand = seeded_db.scalar(
            select(ItemDemandForecast.point_estimate).where(
                ItemDemandForecast.tenant_id == tenant.id,
                ItemDemandForecast.inventory_item_id == choc.id,
                ItemDemandForecast.target_date == target,
            )
        )
        assert float(demand) == pytest.approx(40.0)

    def test_returns_none_with_no_shares(self, seeded_db, tenant):
        result = compute_item_demand(seeded_db, str(tenant.id), "2026-07-20")
        assert result is None


class TestComputeIntradayProfiles:
    def test_computes_hourly_fractions(self, seeded_db, tenant):
        # Seed sales on a Monday at 2pm (40 units) and 3pm (60 units)
        txn1 = SaleTransaction(
            tenant_id=tenant.id,
            external_transaction_id="intra-001",
            source="synthetic",
            timestamp=datetime(
                2026, 7, 20, 14, 0, 0, tzinfo=ZoneInfo("America/Vancouver")
            ),
            total=Decimal("40.00"),
            payment_method="card",
            transaction_type="sale",
        )
        txn2 = SaleTransaction(
            tenant_id=tenant.id,
            external_transaction_id="intra-002",
            source="synthetic",
            timestamp=datetime(
                2026, 7, 20, 15, 0, 0, tzinfo=ZoneInfo("America/Vancouver")
            ),
            total=Decimal("60.00"),
            payment_method="card",
            transaction_type="sale",
        )
        seeded_db.add_all([txn1, txn2])
        seeded_db.flush()

        seeded_db.add(
            SaleLineItem(
                tenant_id=tenant.id,
                sale_transaction_id=txn1.id,
                item_name="Item A",
                quantity=40,
                unit_price=Decimal("1.00"),
            )
        )
        seeded_db.add(
            SaleLineItem(
                tenant_id=tenant.id,
                sale_transaction_id=txn2.id,
                item_name="Item B",
                quantity=60,
                unit_price=Decimal("1.00"),
            )
        )
        seeded_db.commit()

        compute_intraday_profiles(seeded_db, str(tenant.id), "2026-07-21")

        profile_2pm = seeded_db.scalar(
            select(IntradayProfile.fraction).where(
                IntradayProfile.tenant_id == tenant.id,
                IntradayProfile.day_of_week == 0,
                IntradayProfile.hour == 14,
            )
        )
        profile_3pm = seeded_db.scalar(
            select(IntradayProfile.fraction).where(
                IntradayProfile.tenant_id == tenant.id,
                IntradayProfile.day_of_week == 0,
                IntradayProfile.hour == 15,
            )
        )
        assert float(profile_2pm) == pytest.approx(0.4)
        assert float(profile_3pm) == pytest.approx(0.6)

    def test_fractions_sum_to_one(self, seeded_db, tenant):
        # Seed sales across 3 hours on a Tuesday
        for hour, qty in [(10, 20), (12, 50), (14, 30)]:
            txn = SaleTransaction(
                tenant_id=tenant.id,
                external_transaction_id=f"sum-{hour}",
                source="synthetic",
                timestamp=datetime(
                    2026, 7, 21, hour, 0, 0, tzinfo=ZoneInfo("America/Vancouver")
                ),
                total=Decimal(str(qty)),
                payment_method="card",
                transaction_type="sale",
            )
            seeded_db.add(txn)
            seeded_db.flush()
            seeded_db.add(
                SaleLineItem(
                    tenant_id=tenant.id,
                    sale_transaction_id=txn.id,
                    item_name=f"Item-{hour}",
                    quantity=qty,
                    unit_price=Decimal("1.00"),
                )
            )
        seeded_db.commit()

        compute_intraday_profiles(seeded_db, str(tenant.id), "2026-07-22")

        profiles = seeded_db.scalars(
            select(IntradayProfile).where(
                IntradayProfile.tenant_id == tenant.id,
                IntradayProfile.day_of_week == 1,
            )
        ).all()
        total = sum(float(p.fraction) for p in profiles)
        assert total == pytest.approx(1.0)

    def test_returns_none_with_no_data(self, seeded_db, tenant):
        result = compute_intraday_profiles(seeded_db, str(tenant.id), "2026-07-21")
        assert result is None
