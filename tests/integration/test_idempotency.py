from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from src.consumers.email_consumer import process_events as email_process_events
from src.consumers.sales_consumer import process_events as sales_process_events
from src.consumers.stock_updater import process_events as stock_process_events
from src.consumers.summary_consumer import process_events as summary_process_events
from src.db.models import (
    Anomaly,
    CapabilityState,
    DailyActual,
    EmailOutbox,
    Forecast,
    InventoryItem,
    InventoryTransaction,
    POEvent,
    POLine,
    PurchaseOrder,
    SpendLedger,
    Supplier,
    SupplierItem,
    Tenant,
)
from src.db.session import SessionLocal
from src.schemas.anomaly import AnomalySubject, AnomalyType
from src.schemas.autonomy import AutonomyState
from src.schemas.event import ProcurementEventType, SystemEventType
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.anomaly_service import persist_anomaly
from src.services.forecast_service import (
    actuals_aggregate,
    forecast_seasonal_naive,
)
from src.services.ordering_service.proposals import generate_proposals


def _get_tenant(session):
    return session.scalar(select(Tenant).limit(1))


def _create_approved_po(tenant_id):
    with SessionLocal() as session:
        supplier = session.scalar(
            select(Supplier).where(Supplier.tenant_id == tenant_id)
        )
        si = session.scalar(
            select(SupplierItem).where(SupplierItem.supplier_id == supplier.id)
        )

        po = PurchaseOrder(
            tenant_id=tenant_id,
            supplier_id=supplier.id,
            status=POStatus.APPROVED.value,
            total_value=Decimal("21.25"),
            created_by=OrderBy.OWNER.value,
        )
        session.add(po)
        session.flush()

        session.add(
            POLine(
                tenant_id=tenant_id,
                purchase_order_id=po.id,
                inventory_item_id=si.inventory_item_id,
                supplier_item_id=si.id,
                quantity_ordered=Decimal("5.00"),
                unit_cost=si.cost_per_unit,
            )
        )
        session.add(
            POEvent(
                tenant_id=tenant_id,
                purchase_order_id=po.id,
                from_status=POStatus.PROPOSED.value,
                to_status=POStatus.APPROVED.value,
                changed_by=OrderBy.OWNER.value,
                note="Test approval",
            )
        )
        session.commit()
        return po.id, supplier.id


def test_stock_updater_source_key_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)
    item = seeded_db.scalar(
        select(InventoryItem).where(InventoryItem.tenant_id == tenant.id)
    )
    original_qty = item.quantity_on_hand
    source_key = f"txn-idem-001:0:0:{item.id}"

    event = {
        "id": "stream-stock-idem-001",
        "event_type": "bom_depletion",
        "tenant_id": str(tenant.id),
        "priority": "2",
        "payload": {
            "item_id": str(item.id),
            "quantity": 0.10,
            "transaction_type": "usage",
            "note": "BOM: Single Scoop + Chocolate",
            "source_key": source_key,
        },
    }

    stock_process_events([event])
    stock_process_events([event])

    seeded_db.expire_all()

    txn_count = seeded_db.scalar(
        select(func.count())
        .select_from(InventoryTransaction)
        .where(InventoryTransaction.tenant_id == tenant.id)
        .where(InventoryTransaction.event_id == source_key)
    )
    assert txn_count == 1

    updated = seeded_db.scalar(select(InventoryItem).where(InventoryItem.id == item.id))
    assert updated.quantity_on_hand == original_qty - Decimal("0.10")


def test_sales_consumer_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)

    event = {
        "id": "stream-sale-idem-001",
        "event_type": "sale_completed",
        "tenant_id": str(tenant.id),
        "priority": "2",
        "payload": {
            "external_transaction_id": "txn-idem-dup-001",
            "source": "synthetic",
            "timestamp": "2026-07-28T14:30:00+00:00",
            "total": "4.50",
            "payment_method": "card",
            "transaction_type": "sale",
            "discount_amount": "0",
            "line_items": [
                {
                    "item_name": "Single Scoop",
                    "modifiers": ["Chocolate"],
                    "quantity": 1,
                    "unit_price": "4.50",
                },
            ],
        },
    }

    sales_process_events([event])
    sales_process_events([event])

    from src.db.models import SaleTransaction

    count = seeded_db.scalar(
        select(func.count())
        .select_from(SaleTransaction)
        .where(SaleTransaction.tenant_id == tenant.id)
        .where(SaleTransaction.external_transaction_id == "txn-idem-dup-001")
    )
    assert count == 1


def test_email_consumer_outbox_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)
    po_id, _ = _create_approved_po(tenant.id)

    event = {
        "id": "stream-email-idem-001",
        "event_type": ProcurementEventType.PO_APPROVED.value,
        "tenant_id": str(tenant.id),
        "priority": "2",
        "payload": {
            "purchase_order_id": str(po_id),
            "changed_by": OrderBy.OWNER.value,
        },
    }

    email_process_events([event])
    email_process_events([event])

    seeded_db.expire_all()

    outbox_count = seeded_db.scalar(
        select(func.count())
        .select_from(EmailOutbox)
        .where(EmailOutbox.tenant_id == tenant.id)
        .where(EmailOutbox.idempotency_key == f"po-order-{po_id}")
    )
    assert outbox_count == 1


def test_email_consumer_spend_ledger_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)

    with SessionLocal() as session:
        supplier = session.scalar(
            select(Supplier).where(Supplier.tenant_id == tenant.id)
        )
        si = session.scalar(
            select(SupplierItem).where(SupplierItem.supplier_id == supplier.id)
        )

        session.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )

        po = PurchaseOrder(
            tenant_id=tenant.id,
            supplier_id=supplier.id,
            status=POStatus.APPROVED.value,
            total_value=Decimal("5.00"),
            created_by=OrderBy.SYSTEM.value,
        )
        session.add(po)
        session.flush()

        session.add(
            POLine(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                inventory_item_id=si.inventory_item_id,
                supplier_item_id=si.id,
                quantity_ordered=Decimal("1.00"),
                unit_cost=Decimal("5.00"),
            )
        )
        session.commit()
        po_id = po.id

    event = {
        "id": "stream-email-spend-001",
        "event_type": ProcurementEventType.PO_APPROVED.value,
        "tenant_id": str(tenant.id),
        "priority": "2",
        "payload": {
            "purchase_order_id": str(po_id),
            "changed_by": OrderBy.SYSTEM.value,
        },
    }

    email_process_events([event])
    email_process_events([event])

    seeded_db.expire_all()

    spend_count = seeded_db.scalar(
        select(func.count())
        .select_from(SpendLedger)
        .where(SpendLedger.tenant_id == tenant.id)
        .where(SpendLedger.purchase_order_id == po_id)
    )
    assert spend_count <= 1


def test_summary_consumer_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)
    biz_date = "2026-08-15"

    event = {
        "id": "stream-summary-idem-001",
        "event_type": SystemEventType.ANOMALIES_PROCESSED.value,
        "tenant_id": str(tenant.id),
        "priority": "4",
        "payload": {"business_date": biz_date},
    }

    summary_process_events([event])
    summary_process_events([event])

    seeded_db.expire_all()

    count = seeded_db.scalar(
        select(func.count())
        .select_from(EmailOutbox)
        .where(EmailOutbox.tenant_id == tenant.id)
        .where(EmailOutbox.idempotency_key == f"summary-{tenant.id}-{biz_date}")
    )
    assert count == 1


def test_anomaly_persist_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)
    biz_date = date(2026, 8, 16)

    with SessionLocal() as session:
        persist_anomaly(
            session,
            str(tenant.id),
            AnomalyType.FORECAST_RESIDUAL,
            AnomalySubject.TOTAL_UNITS,
            2,
            biz_date,
            {"predicted_min": 80, "predicted_max": 120, "actual": 150},
            "Units 25% above forecast high band",
            48,
        )

    with SessionLocal() as session:
        persist_anomaly(
            session,
            str(tenant.id),
            AnomalyType.FORECAST_RESIDUAL,
            AnomalySubject.TOTAL_UNITS,
            2,
            biz_date,
            {"predicted_min": 80, "predicted_max": 120, "actual": 155},
            "Units 29% above forecast high band",
            48,
        )

    seeded_db.expire_all()

    count = seeded_db.scalar(
        select(func.count())
        .select_from(Anomaly)
        .where(Anomaly.tenant_id == tenant.id)
        .where(Anomaly.anomaly_type == AnomalyType.FORECAST_RESIDUAL)
        .where(Anomaly.subject == AnomalySubject.TOTAL_UNITS)
        .where(Anomaly.business_date == biz_date)
    )
    assert count == 1

    row = seeded_db.scalar(
        select(Anomaly)
        .where(Anomaly.tenant_id == tenant.id)
        .where(Anomaly.business_date == biz_date)
        .where(Anomaly.anomaly_type == AnomalyType.FORECAST_RESIDUAL)
    )
    assert row.evidence["actual"] == 155  # second call updated evidence


def test_actuals_aggregate_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)
    biz_date = "2026-08-14"

    with SessionLocal() as session:
        actuals_aggregate(session, str(tenant.id), biz_date)

    with SessionLocal() as session:
        actuals_aggregate(session, str(tenant.id), biz_date)

    seeded_db.expire_all()

    count = seeded_db.scalar(
        select(func.count())
        .select_from(DailyActual)
        .where(DailyActual.tenant_id == tenant.id)
        .where(DailyActual.actual_date == date.fromisoformat(biz_date))
    )
    assert count <= 2  # at most one per series (units + revenue)


def test_forecast_seasonal_naive_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)
    biz_date = "2026-08-14"

    with SessionLocal() as session:
        actuals_aggregate(session, str(tenant.id), biz_date)

    with SessionLocal() as session:
        forecast_seasonal_naive(session, str(tenant.id), biz_date)

    count_after_first = seeded_db.scalar(
        select(func.count())
        .select_from(Forecast)
        .where(Forecast.tenant_id == tenant.id)
        .where(Forecast.model_version == "seasonal_naive")
        .where(Forecast.forecast_date == date.fromisoformat(biz_date))
    )

    with SessionLocal() as session:
        forecast_seasonal_naive(session, str(tenant.id), biz_date)

    seeded_db.expire_all()

    count_after_second = seeded_db.scalar(
        select(func.count())
        .select_from(Forecast)
        .where(Forecast.tenant_id == tenant.id)
        .where(Forecast.model_version == "seasonal_naive")
        .where(Forecast.forecast_date == date.fromisoformat(biz_date))
    )
    assert count_after_first == count_after_second


def test_generate_proposals_idempotent(seeded_db):
    tenant = _get_tenant(seeded_db)

    item_ids = [
        str(iid)
        for iid in seeded_db.scalars(
            select(InventoryItem.id).where(InventoryItem.tenant_id == tenant.id)
        ).all()
    ]

    with SessionLocal() as session:
        generate_proposals(session, tenant.id, item_ids)

    po_count_after_first = seeded_db.scalar(
        select(func.count())
        .select_from(PurchaseOrder)
        .where(PurchaseOrder.tenant_id == tenant.id)
        .where(PurchaseOrder.created_by == OrderBy.SYSTEM.value)
    )

    seeded_db.expire_all()

    with SessionLocal() as session:
        generate_proposals(session, tenant.id, item_ids)

    seeded_db.expire_all()

    po_count_after_second = seeded_db.scalar(
        select(func.count())
        .select_from(PurchaseOrder)
        .where(PurchaseOrder.tenant_id == tenant.id)
        .where(PurchaseOrder.created_by == OrderBy.SYSTEM.value)
    )
    assert po_count_after_first == po_count_after_second
