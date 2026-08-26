"""Enforce the 31-character Excel-safe customer name contract.

Revision ID: 0003_enforce_excel_safe_customer_name
Revises: 0002_unique_normalized_name_index
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_enforce_excel_safe_customer_name"
down_revision = "0002_unique_normalized_name_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    overlong_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM customer WHERE length(name) < 1 OR length(name) > 31")
    ).scalar_one()
    if overlong_count:
        raise RuntimeError(
            "客户名称迁移已中止：现有数据包含超过 31 个字符的名称，未截断或改名。"
        )

    with op.batch_alter_table("customer") as batch_op:
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=100),
            type_=sa.String(length=31),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_customer_name_excel_length", "length(name) BETWEEN 1 AND 31"
        )


def downgrade() -> None:
    with op.batch_alter_table("customer") as batch_op:
        batch_op.drop_constraint("ck_customer_name_excel_length", type_="check")
        batch_op.alter_column(
            "name",
            existing_type=sa.String(length=31),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
