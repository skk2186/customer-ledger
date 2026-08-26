"""Persistent data model for stage one and later accounting stages."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.types import TypeDecorator

from .extensions import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictInteger(TypeDecorator):
    """Reject floats and booleans before amount/quantity values reach the database."""

    impl = db.Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("金额和数量必须使用整数单位。")
        return value


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customer"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    notes = db.Column(db.Text, nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")

    shipments = db.relationship("Shipment", back_populates="customer")
    payments = db.relationship("Payment", back_populates="customer")


class Shipment(TimestampMixin, db.Model):
    __tablename__ = "shipment"
    __table_args__ = (
        CheckConstraint("total_amount_cents >= 0", name="ck_shipment_total_nonnegative"),
        CheckConstraint("freight_cents >= 0", name="ck_shipment_freight_nonnegative"),
        CheckConstraint("unloading_fee_cents >= 0", name="ck_shipment_unloading_nonnegative"),
        CheckConstraint(
            "returned_pallet_tonnage_hundredths >= 0",
            name="ck_shipment_tonnage_nonnegative",
        ),
        CheckConstraint(
            "returned_pallet_amount_cents >= 0", name="ck_shipment_returned_nonnegative"
        ),
        CheckConstraint("issue_deduction_cents >= 0", name="ck_shipment_issue_nonnegative"),
        CheckConstraint("area_hundredths >= 0", name="ck_shipment_area_nonnegative"),
        CheckConstraint("rounding_cents >= 0", name="ck_shipment_rounding_nonnegative"),
    )

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True)
    shipment_date = db.Column(db.Date, nullable=False, default=date.today)
    total_amount_cents = db.Column(StrictInteger, nullable=False, default=0)
    freight_cents = db.Column(StrictInteger, nullable=False, default=0)
    unloading_fee_cents = db.Column(StrictInteger, nullable=False, default=0)
    returned_pallet_tonnage_hundredths = db.Column(StrictInteger, nullable=False, default=0)
    returned_pallet_amount_cents = db.Column(StrictInteger, nullable=False, default=0)
    issue_deduction_cents = db.Column(StrictInteger, nullable=False, default=0)
    area_hundredths = db.Column(StrictInteger, nullable=False, default=0)
    rounding_cents = db.Column(StrictInteger, nullable=False, default=0)
    description = db.Column(db.Text, nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")

    customer = db.relationship("Customer", back_populates="shipments")
    allocations = db.relationship("PaymentAllocation", back_populates="shipment")


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payment"
    __table_args__ = (CheckConstraint("amount_cents >= 0", name="ck_payment_amount_nonnegative"),)

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    amount_cents = db.Column(StrictInteger, nullable=False, default=0)
    payment_method = db.Column(db.String(50), nullable=False, default="")
    description = db.Column(db.Text, nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")

    customer = db.relationship("Customer", back_populates="payments")
    allocations = db.relationship("PaymentAllocation", back_populates="payment")


class PaymentAllocation(TimestampMixin, db.Model):
    __tablename__ = "payment_allocation"
    __table_args__ = (
        CheckConstraint(
            "allocated_amount_cents >= 0", name="ck_allocation_amount_nonnegative"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payment.id"), nullable=False, index=True)
    shipment_id = db.Column(db.Integer, db.ForeignKey("shipment.id"), nullable=False, index=True)
    allocated_amount_cents = db.Column(StrictInteger, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")

    payment = db.relationship("Payment", back_populates="allocations")
    shipment = db.relationship("Shipment", back_populates="allocations")


class AuditEvent(TimestampMixin, db.Model):
    __tablename__ = "audit_event"

    id = db.Column(db.Integer, primary_key=True)
    object_type = db.Column(db.String(50), nullable=False, index=True)
    object_id = db.Column(db.String(50), nullable=False, index=True)
    action = db.Column(db.String(50), nullable=False)
    before_summary = db.Column(db.Text, nullable=False, default="")
    after_summary = db.Column(db.Text, nullable=False, default="")


class ImportRecord(TimestampMixin, db.Model):
    __tablename__ = "import_record"
    __table_args__ = (
        UniqueConstraint("source_name", "source_key", name="uq_import_source_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    source_name = db.Column(db.String(255), nullable=False)
    source_key = db.Column(db.String(255), nullable=False)
    source_hash = db.Column(db.String(128), nullable=False, default="")
    status = db.Column(db.String(30), nullable=False, default="pending")
    message = db.Column(db.Text, nullable=False, default="")
