"""Align the normalized customer name index with the model.

Revision ID: 0002_unique_normalized_name_index
Revises: 0001_foundation
"""

from alembic import op

revision = "0002_unique_normalized_name_index"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_customer_normalized_name", table_name="customer")
    op.create_index("ix_customer_normalized_name", "customer", ["normalized_name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_customer_normalized_name", table_name="customer")
    op.create_index("ix_customer_normalized_name", "customer", ["normalized_name"], unique=False)
