import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.clock import get_now
from src.db.models import (
    BOMLine,
    CatalogItem,
    CatalogModifier,
    InventoryItem,
    SaleLineItem,
    SaleTransaction,
    Tenant,
)
from src.schemas.sale import SaleTransactionType

LOOKBACK_WEEKS = 8


def compute_newsvendor_ratios(
    session: Session,
    tenant_id: str,
) -> dict[uuid.UUID, Decimal]:
    utc_now = get_now()
    timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    tz = ZoneInfo(timezone)
    today = utc_now.astimezone(tz).date()
    start = datetime.combine(
        today - timedelta(weeks=LOOKBACK_WEEKS), dt_time.min, tzinfo=tz
    )
    end = datetime.combine(today, dt_time.min, tzinfo=tz)

    sales = session.scalars(
        select(SaleLineItem)
        .join(SaleTransaction, SaleLineItem.sale_transaction_id == SaleTransaction.id)
        .where(SaleTransaction.timestamp >= start)
        .where(SaleTransaction.timestamp <= end)
        .where(SaleTransaction.tenant_id == tenant_id)
        .where(SaleTransaction.transaction_type == SaleTransactionType.SALE.value)
    ).all()

    if not sales:
        return {}

    catalog_items = {
        ci.name: ci
        for ci in session.scalars(
            select(CatalogItem).where(CatalogItem.tenant_id == tenant_id)
        ).all()
    }

    modifiers = {
        m.name: m
        for m in session.scalars(
            select(CatalogModifier).where(CatalogModifier.tenant_id == tenant_id)
        ).all()
    }

    all_bom_lines = session.scalars(
        select(BOMLine).where(BOMLine.tenant_id == tenant_id)
    ).all()

    bom_by_key = {}
    bom_always_on = defaultdict(list)
    for bl in all_bom_lines:
        if bl.catalog_modifier_id is None:
            bom_always_on[bl.catalog_item_id].append(bl)
        else:
            bom_by_key[(bl.catalog_item_id, bl.catalog_modifier_id)] = bl

    inv_items = {
        item.id: item
        for item in session.scalars(
            select(InventoryItem).where(InventoryItem.tenant_id == tenant_id)
        ).all()
    }

    total_margin = defaultdict(Decimal)
    total_units = defaultdict(Decimal)

    for sale_item in sales:
        catalog_item = catalog_items.get(sale_item.item_name)
        if not catalog_item:
            continue

        depletions = []  # list of (inventory_item_id, quantity_consumed)

        for mod_name in sale_item.modifiers:
            modifier = modifiers.get(mod_name)
            if not modifier:
                continue

            bom_line = bom_by_key.get((catalog_item.id, modifier.id))
            if not bom_line:
                continue

            depletions.append((bom_line.inventory_item_id, bom_line.quantity))

        for bom_line in bom_always_on.get(catalog_item.id, []):
            depletions.append((bom_line.inventory_item_id, bom_line.quantity))

        if not depletions:
            continue

        sale_cogs = Decimal(0)
        for inv_item_id, qty in depletions:
            inv_item = inv_items.get(inv_item_id)
            if not inv_item:
                continue
            sale_cogs += qty * inv_item.cost_per_unit

        margin = catalog_item.sale_price - sale_cogs
        if margin <= 0:
            continue

        for inv_item_id, qty in depletions:
            inv_item = inv_items.get(inv_item_id)
            if not inv_item:
                continue

            item_cost = qty * inv_item.cost_per_unit
            cost_share = item_cost / sale_cogs if sale_cogs > 0 else Decimal(0)

            total_margin[inv_item_id] += margin * cost_share * sale_item.quantity
            total_units[inv_item_id] += qty * sale_item.quantity

    ratios = {}
    for inv_item_id in total_units:
        if total_units[inv_item_id] <= 0:
            continue

        inv_item = inv_items.get(inv_item_id)
        if not inv_item:
            continue

        margin_per_unit = total_margin[inv_item_id] / total_units[inv_item_id]
        cost = inv_item.cost_per_unit

        if margin_per_unit + cost > 0:
            ratios[inv_item_id] = margin_per_unit / (margin_per_unit + cost)

    return ratios