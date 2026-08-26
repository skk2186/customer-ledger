"""Customer lifecycle operations and audit recording."""

from __future__ import annotations

import json

from sqlalchemy import select

from .models import AuditEvent, Customer, Payment, Shipment
from .validation import ValidationError, normalize_customer_name, validate_customer_name


class BusinessError(ValueError):
    """A safe, user-facing business rule failure."""


def _summary(customer: Customer) -> str:
    """Keep audit data short and avoid copying potentially sensitive notes."""

    return json.dumps(
        {"name": customer.name, "active": customer.active, "has_notes": bool(customer.notes)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _audit(session, customer: Customer, action: str, before: str, after: str) -> None:
    session.add(
        AuditEvent(
            object_type="customer",
            object_id=str(customer.id),
            action=action,
            before_summary=before,
            after_summary=after,
        )
    )


def _validate_and_normalize(name: str) -> tuple[str, str]:
    cleaned = validate_customer_name(name)
    normalized = normalize_customer_name(cleaned)
    if not normalized:
        raise ValidationError("客户名称不能为空。")
    return cleaned, normalized


def create_customer(session, name: str, notes: str = "") -> Customer:
    cleaned, normalized = _validate_and_normalize(name)
    existing = session.scalar(select(Customer).where(Customer.normalized_name == normalized))
    if existing is not None:
        raise BusinessError("客户名称已存在（包括已归档客户）。")
    customer = Customer(name=cleaned, normalized_name=normalized, notes=(notes or "").strip())
    session.add(customer)
    session.flush()
    _audit(session, customer, "created", "", _summary(customer))
    return customer


def update_customer(session, customer: Customer, name: str, notes: str = "") -> Customer:
    cleaned, normalized = _validate_and_normalize(name)
    existing = session.scalar(
        select(Customer).where(
            Customer.normalized_name == normalized,
            Customer.id != customer.id,
        )
    )
    if existing is not None:
        raise BusinessError("客户名称已存在（包括已归档客户）。")
    before = _summary(customer)
    customer.name = cleaned
    customer.normalized_name = normalized
    customer.notes = (notes or "").strip()
    session.flush()
    _audit(session, customer, "updated", before, _summary(customer))
    return customer


def archive_customer(session, customer: Customer) -> Customer:
    if not customer.active:
        return customer
    before = _summary(customer)
    customer.active = False
    session.flush()
    _audit(session, customer, "archived", before, _summary(customer))
    return customer


def restore_customer(session, customer: Customer) -> Customer:
    if customer.active:
        return customer
    before = _summary(customer)
    customer.active = True
    session.flush()
    _audit(session, customer, "restored", before, _summary(customer))
    return customer


def delete_customer(session, customer: Customer) -> None:
    """Only allow physical deletion when no accounting history exists."""

    has_shipments = session.scalar(
        select(Shipment.id).where(Shipment.customer_id == customer.id).limit(1)
    )
    has_payments = session.scalar(
        select(Payment.id).where(Payment.customer_id == customer.id).limit(1)
    )
    if has_shipments is not None or has_payments is not None:
        raise BusinessError("有账务历史的客户不能物理删除，请使用归档。")
    before = _summary(customer)
    session.delete(customer)
    session.flush()
    _audit(session, customer, "deleted", before, "")
