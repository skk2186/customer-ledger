"""Create the stage-one foundation data model.

Revision ID: 0001_foundation
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_name"),
    )
    op.create_index("ix_customer_normalized_name", "customer", ["normalized_name"], unique=False)

    op.create_table(
        "shipment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("shipment_date", sa.Date(), nullable=False),
        sa.Column("total_amount_cents", sa.Integer(), nullable=False),
        sa.Column("freight_cents", sa.Integer(), nullable=False),
        sa.Column("unloading_fee_cents", sa.Integer(), nullable=False),
        sa.Column("returned_pallet_tonnage_hundredths", sa.Integer(), nullable=False),
        sa.Column("returned_pallet_amount_cents", sa.Integer(), nullable=False),
        sa.Column("issue_deduction_cents", sa.Integer(), nullable=False),
        sa.Column("area_hundredths", sa.Integer(), nullable=False),
        sa.Column("rounding_cents", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total_amount_cents >= 0", name="ck_shipment_total_nonnegative"),
        sa.CheckConstraint("freight_cents >= 0", name="ck_shipment_freight_nonnegative"),
        sa.CheckConstraint("unloading_fee_cents >= 0", name="ck_shipment_unloading_nonnegative"),
        sa.CheckConstraint(
            "returned_pallet_tonnage_hundredths >= 0",
            name="ck_shipment_tonnage_nonnegative",
        ),
        sa.CheckConstraint(
            "returned_pallet_amount_cents >= 0", name="ck_shipment_returned_nonnegative"
        ),
        sa.CheckConstraint("issue_deduction_cents >= 0", name="ck_shipment_issue_nonnegative"),
        sa.CheckConstraint("area_hundredths >= 0", name="ck_shipment_area_nonnegative"),
        sa.CheckConstraint("rounding_cents >= 0", name="ck_shipment_rounding_nonnegative"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipment_customer_id", "shipment", ["customer_id"], unique=False)

    op.create_table(
        "payment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("payment_method", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payment_amount_nonnegative"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_customer_id", "payment", ["customer_id"], unique=False)

    op.create_table(
        "payment_allocation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("allocated_amount_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "allocated_amount_cents >= 0", name="ck_allocation_amount_nonnegative"
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payment.id"]),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipment.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_allocation_payment_id", "payment_allocation", ["payment_id"], unique=False
    )
    op.create_index(
        "ix_payment_allocation_shipment_id", "payment_allocation", ["shipment_id"], unique=False
    )

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("object_id", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("before_summary", sa.Text(), nullable=False),
        sa.Column("after_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_event_object_type", "audit_event", ["object_type"], unique=False)
    op.create_index("ix_audit_event_object_id", "audit_event", ["object_id"], unique=False)

    op.create_table(
        "import_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("source_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name", "source_key", name="uq_import_source_key"),
    )


def downgrade() -> None:
    op.drop_table("import_record")
    op.drop_index("ix_audit_event_object_id", table_name="audit_event")
    op.drop_index("ix_audit_event_object_type", table_name="audit_event")
    op.drop_table("audit_event")
    op.drop_index("ix_payment_allocation_shipment_id", table_name="payment_allocation")
    op.drop_index("ix_payment_allocation_payment_id", table_name="payment_allocation")
    op.drop_table("payment_allocation")
    op.drop_index("ix_payment_customer_id", table_name="payment")
    op.drop_table("payment")
    op.drop_index("ix_shipment_customer_id", table_name="shipment")
    op.drop_table("shipment")
    op.drop_index("ix_customer_normalized_name", table_name="customer")
    op.drop_table("customer")
