import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models.inventory import InventoryItem
from src.db.models.suppliers import (
    POLine,
    PurchaseOrder,
    Supplier,
    SupplierItem,
)
from src.schemas.suppliers import POStatus


def generate_proposals(session: Session, tenant_id, item_ids: list[str]):
    po_placed = session.scalars(
        select(POLine)
        .join(PurchaseOrder, POLine.purchase_order_id == PurchaseOrder.id)
        .where(PurchaseOrder.status.in_([POStatus.PROPOSED, POStatus.APPROVED]))
        .where(POLine.tenant_id == tenant_id)
    ).all()
    
    po_inv_ids = [po.inventory_item_id for po in po_placed]
    
    low_items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant_id)
        .where(InventoryItem.id.in_(item_ids))
        .where(InventoryItem.quantity_on_hand <= InventoryItem.reorder_point)
        .where(InventoryItem.id.not_in(po_inv_ids))
    ).all()
    
    if not low_items:
        return

    low_item_ids = [item.id for item in low_items]

    supplier_items = session.scalars(
        select(SupplierItem)
        .join(Supplier, Supplier.id == SupplierItem.supplier_id)
        .where(SupplierItem.tenant_id == tenant_id)
        .where(SupplierItem.inventory_item_id.in_(low_item_ids))
        .where(Supplier.is_active == True)
    ).all()

    supplier_ids = {si.supplier_id for si in supplier_items}
    suppliers = session.scalars(
        select(Supplier).where(Supplier.id.in_(supplier_ids))
    ).all()
    supplier_map = {s.id: s for s in suppliers}

    if not supplier_items:
        return

    best_by_item: dict[uuid.UUID, SupplierItem] = {}
    for si in supplier_items:
        existing = best_by_item.get(si.inventory_item_id)
        if (
            existing is None
            or supplier_map[si.supplier_id].lead_time_days
            < supplier_map[existing.supplier_id].lead_time_days
        ):
            best_by_item[si.inventory_item_id] = si

    supplier_groups = defaultdict(list)
    for si in best_by_item.values():
        supplier_groups[si.supplier_id].append(si)

    for 