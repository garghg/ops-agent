from decimal import Decimal

from sqlalchemy import select

from src.consumers.sales_consumer import process_events
from src.db.models import SaleLineItem, SaleTransaction, Tenant


def _make_event(tenant_id, external_id="txn-001", total="12.50"):
    return {
        "id": "fake-stream-id-001",
        "event_type": "sale_completed",
        "tenant_id": str(tenant_id),
        "priority": "2",
        "payload": {
            "external_transaction_id": external_id,
            "source": "synthetic",
            "timestamp": "2026-07-28T14:30:00+00:00",
            "total": total,
            "payment_method": "card",
            "line_items": [
                {
                    "item_name": "Single Scoop",
                    "modifiers": ["Chocolate"],
                    "quantity": 1,
                    "unit_price": "4.50",
                },
                {
                    "item_name": "Double Scoop",
                    "modifiers": ["Vanilla", "Strawberry"],
                    "quantity": 1,
                    "unit_price": "8.00",
                },
            ],
        },
    }


def test_process_events_persists_sale(seeded_db):
    tenant = seeded_db.scalar(select(Tenant))
    event = _make_event(tenant.id)

    process_events([event])

    txn = seeded_db.scalar(
        select(SaleTransaction).where(
            SaleTransaction.tenant_id == tenant.id,
            SaleTransaction.external_transaction_id == "txn-001",
        )
    )
    assert txn is not None
    assert txn.total == Decimal("12.50")
    assert txn.source == "synthetic"
    assert txn.payment_method == "card"

    lines = seeded_db.scalars(
        select(SaleLineItem).where(
            SaleLineItem.sale_transaction_id == txn.id,
        )
    ).all()
    assert len(lines) == 2

    names = {line.item_name for line in lines}
    assert names == {"Single Scoop", "Double Scoop"}

    chocolate_line = next(l for l in lines if l.item_name == "Single Scoop")
    assert chocolate_line.modifiers == ["Chocolate"]
    assert chocolate_line.quantity == 1
    assert chocolate_line.unit_price == Decimal("4.50")
    assert chocolate_line.tenant_id == tenant.id


def test_process_events_idempotent(seeded_db):
    tenant = seeded_db.scalar(select(Tenant))
    event = _make_event(tenant.id, external_id="txn-dup")

    process_events([event])
    process_events([event])

    count = len(
        seeded_db.scalars(
            select(SaleTransaction).where(
                SaleTransaction.tenant_id == tenant.id,
                SaleTransaction.external_transaction_id == "txn-dup",
            )
        ).all()
    )
    assert count == 1


def test_process_events_bad_payload(seeded_db):
    bad_event = {
        "id": "fake-stream-id-bad",
        "event_type": "sale_completed",
        "tenant_id": "not-a-uuid",
        "priority": "2",
        "payload": {"garbage": True},
    }

    process_events([bad_event])  # should not raise