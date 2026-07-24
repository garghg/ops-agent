from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models.core import Tenant
from src.db.models.inventory import InventoryItem, InventoryTransaction, InventoryTransactionType
from src.db.models.counts import PhysicalCount, CountLine
from src.db.models.shrinkage import ShrinkageRate
from src.services.shrinkage_service import compute_shrinkage_rates


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def items(seeded_db, tenant):
    ice_cream = seeded_db.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.category == "ice_cream")
        .limit(1)
    )
    topping = seeded_db.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.category == "toppings")
        .limit(1)
    )

    ice_cream.quantity_on_hand = Decimal("20.00")
    topping.quantity_on_hand = Decimal("5.00")
    seeded_db.flush()

    return {"ice_cream": ice_cream, "topping": topping}


def _make_count(session, tenant, items, time, counts_dict):
    count = PhysicalCount(tenant_id=tenant.id, counted_by="tester", counted_at=time)
    session.add(count)
    session.flush()

    for key, actual in counts_dict.items():
        item = items[key]
        session.add(CountLine(
            tenant_id=tenant.id,
            physical_count_id=count.id,
            inventory_item_id=item.id,
            expected_quantity=item.quantity_on_hand,
            actual_quantity=actual,
            discrepancy=actual - item.quantity_on_hand,
        ))
        item.quantity_on_hand = actual

    session.flush()
    return count


def _add_depletions(session, item, qty, time):
    session.add(InventoryTransaction(
        tenant_id=item.tenant_id,
        item_id=item.id,
        quantity_change=-abs(qty),
        transaction_type=InventoryTransactionType.USAGE,
        created_at=time,
        event_id=f"test-{item.id}-{time.isoformat()}",
    ))
    session.flush()


class TestFirstCount:
    def test_no_previous_count_returns_early(self, seeded_db, tenant, items):
        now = datetime.now(timezone.utc)
        count = _make_count(seeded_db, tenant, items, now, {
            "ice_cream": Decimal("18.00"),
            "topping": Decimal("4.50"),
        })

        compute_shrinkage_rates(seeded_db, count.id, tenant.id)

        rates = seeded_db.scalars(
            select(ShrinkageRate).where(ShrinkageRate.tenant_id == tenant.id)
        ).all()
        assert len(rates) == 0


class TestSecondCount:
    def test_computes_rate_from_discrepancy_and_depletions(self, seeded_db, tenant, items):
        t1 = datetime.now(timezone.utc) - timedelta(days=7)
        t2 = datetime.now(timezone.utc)

        _make_count(seeded_db, tenant, items, t1, {
            "ice_cream": Decimal("20.00"),
            "topping": Decimal("5.00"),
        })

        _add_depletions(seeded_db, items["ice_cream"], Decimal("10.00"), t1 + timedelta(days=3))
        _add_depletions(seeded_db, items["topping"], Decimal("2.00"), t1 + timedelta(days=3))

        items["ice_cream"].quantity_on_hand = Decimal("10.00")
        items["topping"].quantity_on_hand = Decimal("3.00")

        count2 = _make_count(seeded_db, tenant, items, t2, {
            "ice_cream": Decimal("9.00"),
            "topping": Decimal("2.50"),
        })

        compute_shrinkage_rates(seeded_db, count2.id, tenant.id)

        rates = {r.category: r for r in seeded_db.scalars(
            select(ShrinkageRate).where(ShrinkageRate.tenant_id == tenant.id)
        ).all()}

        assert rates["ice_cream"].rate == pytest.approx(Decimal("0.1"), abs=Decimal("0.001"))
        assert rates["ice_cream"].sample_count == 1

        assert rates["toppings"].rate == pytest.approx(Decimal("0.25"), abs=Decimal("0.001"))
        assert rates["toppings"].sample_count == 1

    def test_no_negative_discrepancy_skips_category(self, seeded_db, tenant, items):
        t1 = datetime.now(timezone.utc) - timedelta(days=7)
        t2 = datetime.now(timezone.utc)

        _make_count(seeded_db, tenant, items, t1, {
            "ice_cream": Decimal("20.00"),
            "topping": Decimal("5.00"),
        })

        _add_depletions(seeded_db, items["ice_cream"], Decimal("5.00"), t1 + timedelta(days=3))

        items["ice_cream"].quantity_on_hand = Decimal("15.00")
        count2 = _make_count(seeded_db, tenant, items, t2, {
            "ice_cream": Decimal("16.00"),
            "topping": Decimal("5.00"),
        })

        compute_shrinkage_rates(seeded_db, count2.id, tenant.id)

        rates = seeded_db.scalars(
            select(ShrinkageRate).where(ShrinkageRate.tenant_id == tenant.id)
        ).all()
        assert len(rates) == 0

    def test_no_depletions_skips_category(self, seeded_db, tenant, items):
        t1 = datetime.now(timezone.utc) - timedelta(days=7)
        t2 = datetime.now(timezone.utc)

        _make_count(seeded_db, tenant, items, t1, {
            "ice_cream": Decimal("20.00"),
            "topping": Decimal("5.00"),
        })

        items["ice_cream"].quantity_on_hand = Decimal("20.00")
        count2 = _make_count(seeded_db, tenant, items, t2, {
            "ice_cream": Decimal("18.00"),
            "topping": Decimal("5.00"),
        })

        compute_shrinkage_rates(seeded_db, count2.id, tenant.id)

        rates = seeded_db.scalars(
            select(ShrinkageRate).where(ShrinkageRate.tenant_id == tenant.id)
        ).all()
        assert len(rates) == 0


class TestRunningAverage:
    def test_third_count_updates_average(self, seeded_db, tenant, items):
        t1 = datetime.now(timezone.utc) - timedelta(days=14)
        t2 = t1 + timedelta(days=7)
        t3 = datetime.now(timezone.utc)

        _make_count(seeded_db, tenant, items, t1, {
            "ice_cream": Decimal("20.00"),
            "topping": Decimal("5.00"),
        })

        _add_depletions(seeded_db, items["ice_cream"], Decimal("10.00"), t1 + timedelta(days=3))
        items["ice_cream"].quantity_on_hand = Decimal("10.00")

        count2 = _make_count(seeded_db, tenant, items, t2, {
            "ice_cream": Decimal("9.00"),
        })
        compute_shrinkage_rates(seeded_db, count2.id, tenant.id)

        _add_depletions(seeded_db, items["ice_cream"], Decimal("5.00"), t2 + timedelta(days=3))
        items["ice_cream"].quantity_on_hand = Decimal("4.00")

        count3 = _make_count(seeded_db, tenant, items, t3, {
            "ice_cream": Decimal("3.50"),
        })
        compute_shrinkage_rates(seeded_db, count3.id, tenant.id)

        rate = seeded_db.scalar(
            select(ShrinkageRate)
            .where(ShrinkageRate.tenant_id == tenant.id)
            .where(ShrinkageRate.category == "ice_cream")
        )

        assert rate.rate == pytest.approx(Decimal("0.1"), abs=Decimal("0.001"))
        assert rate.sample_count == 2