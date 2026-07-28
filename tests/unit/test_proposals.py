from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    InventoryItem,
    POEvent,
    POLine,
    Supplier,
    Tenant,
)
from src.schemas.suppliers import POStatus
from src.services.ordering_service import generate_proposals


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


class TestNoLowItems:
    def test_returns_empty_when_all_items_above_reorder_point(self, seeded_db, tenant):
        items = seeded_db.scalars(
            select(InventoryItem).where(InventoryItem.tenant_id == tenant.id)
        ).all()

        for item in items:
            item.quantity_on_hand = item.reorder_point + Decimal("10.00")
        seeded_db.flush()

        item_ids = [str(item.id) for item in items]
        result = generate_proposals(seeded_db, tenant.id, item_ids)
        assert result == []


class TestBasicProposal:
    def test_creates_po_with_correct_pack_rounding_and_total(self, seeded_db, tenant):
        item = seeded_db.scalar(
            select(InventoryItem)
            .where(InventoryItem.tenant_id == tenant.id)
            .where(InventoryItem.name == "Chocolate Ice Cream")
        )

        supplier = seeded_db.scalar(
            select(Supplier)
            .where(Supplier.tenant_id == tenant.id)
            .where(Supplier.name == "Dairy Farms Co-op")
        )

        result = generate_proposals(seeded_db, tenant.id, [str(item.id)])

        assert len(result) == 1
        assert result[0].supplier_id == supplier.id
        assert result[0].total_value == Decimal("67.50")

        po_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == result[0].id)
        )
        assert po_line.quantity_ordered == Decimal("15.00")
        assert po_line.unit_cost == Decimal("4.50")

        event = seeded_db.scalar(
            select(POEvent).where(POEvent.purchase_order_id == result[0].id)
        )
        assert event.to_status == POStatus.PROPOSED.value
        assert event.changed_by == "system"


class TestSkipApproved:
    def test_does_not_repropose_item_on_approved_po(self, seeded_db, tenant):
        item = seeded_db.scalar(
            select(InventoryItem)
            .where(InventoryItem.tenant_id == tenant.id)
            .where(InventoryItem.name == "Chocolate Ice Cream")
        )

        proposed = generate_proposals(seeded_db, tenant.id, [str(item.id)])
        assert len(proposed) == 1

        proposed[0].status = POStatus.APPROVED.value
        seeded_db.flush()

        result = generate_proposals(seeded_db, tenant.id, [str(item.id)])
        assert len(result) == 0


class TestUpdateExistingProposal:
    def test_updates_line_quantity_instead_of_creating_new_po(self, seeded_db, tenant):
        item = seeded_db.scalar(
            select(InventoryItem)
            .where(InventoryItem.tenant_id == tenant.id)
            .where(InventoryItem.name == "Chocolate Ice Cream")
        )

        first_run = generate_proposals(seeded_db, tenant.id, [str(item.id)])
        assert len(first_run) == 1

        po_id = first_run[0].id
        first_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == po_id)
        )
        assert first_line.quantity_ordered == Decimal("15.00")

        item.quantity_on_hand = Decimal("2.00")
        seeded_db.flush()

        second_run = generate_proposals(seeded_db, tenant.id, [str(item.id)])
        assert len(second_run) == 1
        assert second_run[0].id == po_id

        updated_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == po_id)
        )
        assert updated_line.quantity_ordered == Decimal("20.00")
        assert second_run[0].total_value == Decimal("90.00")

        events = seeded_db.scalars(
            select(POEvent).where(POEvent.purchase_order_id == po_id)
        ).all()
        assert len(events) == 2


class TestBelowMinimumSuggestsTopups:
    def test_populates_suggested_topups_when_below_minimum(self, seeded_db, tenant):
        item = seeded_db.scalar(
            select(InventoryItem)
            .where(InventoryItem.tenant_id == tenant.id)
            .where(InventoryItem.name == "Rainbow Sprinkles")
        )
        item.quantity_on_hand = Decimal("1.00")
        item.reorder_point = Decimal("2.00")
        item.target_quantity = Decimal("3.00")
        seeded_db.flush()

        result = generate_proposals(seeded_db, tenant.id, [str(item.id)])

        assert len(result) == 1
        assert result[0].total_value == Decimal("12.00")
        assert result[0].suggested_topups is not None
        assert len(result[0].suggested_topups) > 0

        suggested_names = [s["name"] for s in result[0].suggested_topups]
        assert "Rainbow Sprinkles" not in suggested_names