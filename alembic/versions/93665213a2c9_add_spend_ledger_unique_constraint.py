"""add spend ledger unique constraint

Revision ID: 93665213a2c9
Revises: 63a719abb6d6
Create Date: 2026-08-18 10:31:57.527590

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93665213a2c9'
down_revision: Union[str, Sequence[str], None] = '63a719abb6d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "spend_ledger_tenant_purchase_order_key",
        "spend_ledger",
        ["tenant_id", "purchase_order_id"],
    )

def downgrade() -> None:
    op.drop_constraint(
        "spend_ledger_tenant_purchase_order_key",
        "spend_ledger",
        type_="unique",
    )
