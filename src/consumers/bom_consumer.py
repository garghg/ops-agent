import time
import redis
from sqlalchemy import select
from src.logging import setup_logging, get_logger
from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.schemas.inventory import InventoryEventPayload, InventoryTransactionType
from src.db.models import BOMLine, CatalogItem, CatalogModifier, MappingGap
from src.schemas.sale import SaleEvent
from src.schemas.event import ConsumerGroup, EventCategory, InventoryEventType
from src.events.bus import claim_pending_events, publish_event, r, read_event
from src.db.session import SessionLocal

SALES_STREAM = f"{EventCategory.SALES.value}_events"
log = get_logger(__name__)


def process_events(events: list[dict]) -> None:
    for event in events:
        try:
            sale = SaleEvent(**event["payload"])
            tenant_id = event["tenant_id"]
        except Exception as e:
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(SALES_STREAM, ConsumerGroup.BOM_CONSUMER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                for line_item in sale.line_items:
                    catalog_item = session.scalar(
                        select(CatalogItem).where(
                            CatalogItem.name == line_item.item_name,
                            CatalogItem.tenant_id == tenant_id,
                        )
                    )

                    if catalog_item is None:
                        log.info(
                            "Unmapped catalog item",
                            item_name=line_item.item_name,
                            tenant_id=str(tenant_id),
                        )
                        session.add(
                            MappingGap(
                                tenant_id=tenant_id,
                                external_item_name=line_item.item_name,
                                external_modifier_name=None,
                                source=sale.source,
                            )
                        )
                        session.commit()
                        continue

                    for mod_name in line_item.modifiers:
                        modifier = session.scalar(
                            select(CatalogModifier).where(
                                CatalogModifier.name == mod_name,
                                CatalogModifier.tenant_id == tenant_id,
                            )
                        )

                        if modifier is None:
                            log.info(
                                "Unmapped modifier",
                                item_name=line_item.item_name,
                                modifier_name=mod_name,
                                tenant_id=str(tenant_id),
                            )
                            session.add(
                                MappingGap(
                                    tenant_id=tenant_id,
                                    external_item_name=line_item.item_name,
                                    external_modifier_name=mod_name,
                                    source=sale.source,
                                )
                            )
                            session.commit()
                            continue

                        bom_line = session.scalar(
                            select(BOMLine).where(
                                BOMLine.catalog_item_id == catalog_item.id,
                                BOMLine.catalog_modifier_id == modifier.id,
                                BOMLine.tenant_id == tenant_id,
                            )
                        )

                        if bom_line is None:
                            log.info(
                                "Missing BOM line",
                                item_name=line_item.item_name,
                                modifier_name=mod_name,
                                tenant_id=str(tenant_id),
                            )
                            session.add(
                                MappingGap(
                                    tenant_id=tenant_id,
                                    external_item_name=line_item.item_name,
                                    external_modifier_name=modifier.name,
                                    source=sale.source,
                                )
                            )
                            session.commit()
                            continue

                        publish_event(
                            EventCategory.INVENTORY,
                            InventoryEventType.BOM_DEPLETION.value,
                            "2",
                            InventoryEventPayload(
                                item_id=bom_line.inventory_item_id,
                                quantity=bom_line.quantity * line_item.quantity,
                                transaction_type=InventoryTransactionType.USAGE,
                                note=f"BOM: {line_item.item_name} + {mod_name}",
                            ).model_dump(mode="json"),
                            str(tenant_id),
                        )

                    always_lines = session.scalars(
                        select(BOMLine).where(
                            BOMLine.catalog_item_id == catalog_item.id,
                            BOMLine.catalog_modifier_id.is_(None),
                            BOMLine.tenant_id == tenant_id,
                        )
                    ).all()

                    for bom_line in always_lines:
                        publish_event(
                            EventCategory.INVENTORY,
                            InventoryEventType.BOM_DEPLETION.value,
                            "2",
                            InventoryEventPayload(
                                item_id=bom_line.inventory_item_id,
                                quantity=bom_line.quantity * line_item.quantity,
                                transaction_type=InventoryTransactionType.USAGE,
                                note=f"BOM: {line_item.item_name} (always)",
                            ).model_dump(mode="json"),
                            str(tenant_id),
                        )

        except Exception as e:
            print(f"Error processing event {event['id']}: {e}")
            r.xack(SALES_STREAM, ConsumerGroup.BOM_CONSUMER.value, event["id"])
            continue

        r.xack(SALES_STREAM, ConsumerGroup.BOM_CONSUMER.value, event["id"])


def bom_consumer() -> None:
    last_claim_check = 0.0

    while True:
        try:
            events = read_event(
                EventCategory.SALES,
                ConsumerGroup.BOM_CONSUMER.value,
                CONSUMER_NAME,
            )
        except redis.exceptions.TimeoutError:
            events = []

        process_events(events)

        now = time.monotonic()
        if now - last_claim_check >= CLAIM_INTERVAL_SECONDS:
            last_claim_check = now
            claimed_events = claim_pending_events(
                EventCategory.SALES,
                ConsumerGroup.BOM_CONSUMER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)


if __name__ == "__main__":
    setup_logging()
    bom_consumer()
