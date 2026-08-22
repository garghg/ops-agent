from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    Category,
    CorrectionFactor,
    CountLine,
    InventoryItem,
    InventoryTransaction,
    PhysicalCount,
    Tenant,
)
from src.schemas.inventory import InventoryTransactionType
from src.schemas.learning import FactorKind
from src.services.shrinkage_service import compute_shrinkage_rates


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def items(seeded_db, tenant):
    ice_cream = seeded_db.scalar(
        select(InventoryItem)
        .join(Category, InventoryItem.category_id == Category.id)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(Category.name == "ice_cream")
        .limit(1)
    )

    topping = seeded_db.scalar(
        select(InventoryItem)
        .join(Category, InventoryItem.category_id == Category.id)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(Category.name == "toppings")
        .limit(1)
    )

    ice_cream.quantity_on_hand = Decimal("20.00")
    topping.quantity_on_hand = Decimal("5.00")
    seeded_db.flush()

    return {"ice_cream": ice_cream, "topping": topping}


def _get_shrinkage_factors(session, tenant_id):
    factors = session.scalars(
        select(CorrectionFactor)
        .where(CorrectionFactor.tenant_id == tenant_id)
        .where(CorrectionFactor.kind == FactorKind.SHRINKAGE)
    ).all()

    cat_ids = [f.scope_key for f in factors]
    categories = session.scalars(
        select(Category).where(Category.id.in_(cat_ids))
    ).all()
    cat_map = {str(c.id): c.name for c in categories}

    return {cat_map[f.scope_key]: f for f in factors}


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
        occurred_at=time,
        event_id=f"test-{item.id}-{time.isoformat()}",
    ))
    session.flush()


class TestFirstCount:
    def test_no_previous_count_returns_early(self, seeded_db, tenant, items):
        now = datetime.now(UTC)
        count = _make_count(seeded_db, tenant, items, now, {
            "ice_cream": Decimal("18.00"),
            "topping": Decimal("4.50"),
        })

        compute_shrinkage_rates(seeded_db, count.id, tenant.id)

        rates = _get_shrinkage_factors(seeded_db, tenant.id)
        assert len(rates) == 0


class TestSecondCount:
    def test_computes_rate_from_discrepancy_and_depletions(self, seeded_db, tenant, items):
        t1 = datetime.now(UTC) - timedelta(days=7)
        t2 = datetime.now(UTC)

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

        rates = _get_shrinkage_factors(seeded_db, tenant.id)

        assert float(rates["ice_cream"].value) == pytest.approx(0.1, abs=0.001)
        assert rates["ice_cream"].evidence_count == 1

        assert float(rates["toppings"].value) == pytest.approx(0.25, abs=0.001)
        assert rates["toppings"].evidence_count == 1

    def test_no_negative_discrepancy_skips_category(self, seeded_db, tenant, items):
        t1 = datetime.now(UTC) - timedelta(days=7)
        t2 = datetime.now(UTC)

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

        rates = _get_shrinkage_factors(seeded_db, tenant.id)
        assert len(rates) == 0

    def test_no_depletions_skips_category(self, seeded_db, tenant, items):
        t1 = datetime.now(UTC) - timedelta(days=7)
        t2 = datetime.now(UTC)

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

        rates = _get_shrinkage_factors(seeded_db, tenant.id)
        assert len(rates) == 0


class TestRunningAverage:
    def test_third_count_updates_with_ewma(self, seeded_db, tenant, items):
        t1 = datetime.now(UTC) - timedelta(days=14)
        t2 = t1 + timedelta(days=7)
        t3 = datetime.now(UTC)

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

        rates = _get_shrinkage_factors(seeded_db, tenant.id)
        ice_cream = rates["ice_cream"]

        assert ice_cream.evidence_count == 2
        # Both observations are 0.1, so EWMA should stay near 0.1
        assert float(ice_cream.value) == pytest.approx(0.1, abs=0.01)