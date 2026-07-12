"""split_adjustment_transaction_type

Revision ID: d4922f03d029
Revises: dc6f317d1d5c
Create Date: 2026-07-11 17:33:09.034492

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4922f03d029'
down_revision: Union[str, Sequence[str], None] = 'dc6f317d1d5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("inventory_transactions_transaction_type_check", "inventory_transactions", type_="check")
    op.create_check_constraint(
        "inventory_transactions_transaction_type_check",
        "inventory_transactions",
        "transaction_type IN ('restock', 'usage', 'waste', 'adjustment_add', 'adjustment_sub')",
    )

def downgrade() -> None:
    op.drop_constraint("inventory_transactions_transaction_type_check", "inventory_transactions", type_="check")
    op.create_check_constraint(
        "inventory_transactions_transaction_type_check",
        "inventory_transactions",
        "transaction_type IN ('restock', 'usage', 'waste', 'adjustment')",
    )