"""Add bookkeeping idempotency and positive payment enforcement.

Revision ID: 0004_bookkeeping_workflow
Revises: 0003_enforce_excel_safe_customer_name
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_bookkeeping_workflow"
down_revision = "0003_enforce_excel_safe_customer_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("payment") as batch_op:
        batch_op.drop_constraint("ck_payment_amount_nonnegative", type_="check")
        batch_op.create_check_constraint("ck_payment_amount_positive", "amount_cents > 0")

    op.create_table(
        "submission_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=50), nullable=False),
        sa.Column("result_type", sa.String(length=50), nullable=False),
        sa.Column("result_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_submission_record_token", "submission_record", ["token"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_submission_record_token", table_name="submission_record")
    op.drop_table("submission_record")
    with op.batch_alter_table("payment") as batch_op:
        batch_op.drop_constraint("ck_payment_amount_positive", type_="check")
        batch_op.create_check_constraint("ck_payment_amount_nonnegative", "amount_cents >= 0")
