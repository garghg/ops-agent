from decimal import Decimal

from sqlalchemy import func, select

from src.db.models import SaleTransaction
from src.schemas.sale import SaleTransactionType


def get_sales_summary(session, tenant_id, day_start, day_end):
    return session.execute(
        select(
            func.count().label("transaction_count"),
            func.coalesce(
                func.sum(SaleTransaction.total), Decimal(0)
            ).label("revenue"),
            func.coalesce(
                func.sum(SaleTransaction.discount_amount), Decimal(0)
            ).label("total_discounts"),
            func.count()
            .filter(
                SaleTransaction.transaction_type == SaleTransactionType.VOID
            )
            .label("void_count"),
            func.count()
            .filter(
                SaleTransaction.transaction_type
                == SaleTransactionType.REFUND
            )
            .label("refund_count"),
        ).where(
            SaleTransaction.tenant_id == tenant_id,
            SaleTransaction.timestamp >= day_start,
            SaleTransaction.timestamp < day_end,
        )
    ).first()