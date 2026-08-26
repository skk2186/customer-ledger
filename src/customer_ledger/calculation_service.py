"""The single source of truth for all accounting calculations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from .models import Customer, Payment, PaymentAllocation, Shipment


@dataclass(frozen=True)
class ShipmentCalculation:
    receivable_cents: int
    received_cents: int
    balance_cents: int


def calculate_receivable(shipment: Shipment) -> int:
    """Calculate receivable in cents; negative results are intentionally retained."""

    return (
        shipment.total_amount_cents
        - shipment.freight_cents
        - shipment.unloading_fee_cents
        - shipment.returned_pallet_amount_cents
        - shipment.issue_deduction_cents
    )


def effective_allocated_cents(session, shipment_id: int) -> int:
    value = session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.allocated_amount_cents), 0))
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .where(
            PaymentAllocation.shipment_id == shipment_id,
            PaymentAllocation.active.is_(True),
            Payment.active.is_(True),
        )
    )
    return int(value or 0)


def calculate_shipment(session, shipment: Shipment) -> ShipmentCalculation:
    received = effective_allocated_cents(session, shipment.id)
    receivable = calculate_receivable(shipment)
    return ShipmentCalculation(
        receivable_cents=receivable,
        received_cents=received,
        balance_cents=receivable - received - shipment.rounding_cents,
    )


def effective_payment_cents(session, customer_id: int) -> int:
    value = session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.customer_id == customer_id,
            Payment.active.is_(True),
        )
    )
    return int(value or 0)


def customer_total_received(session, customer: Customer) -> int:
    return effective_payment_cents(session, customer.id)


def customer_net_balance(session, customer: Customer) -> int:
    shipments = session.scalars(
        select(Shipment).where(Shipment.customer_id == customer.id, Shipment.active.is_(True))
    )
    receivable = sum(calculate_receivable(shipment) for shipment in shipments)
    rounding = session.scalar(
        select(func.coalesce(func.sum(Shipment.rounding_cents), 0)).where(
            Shipment.customer_id == customer.id,
            Shipment.active.is_(True),
        )
    )
    return receivable - effective_payment_cents(session, customer.id) - int(rounding or 0)


def validate_payment_allocation(
    session, payment: Payment, shipment: Shipment, amount_cents: int
) -> None:
    """Validate a new allocation before it is persisted."""

    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise ValueError("分配金额必须使用整数分。")
    if amount_cents < 0:
        raise ValueError("分配金额不能为负数。")
    if not payment.active or not shipment.active:
        raise ValueError("只能给有效的付款和发货建立分配。")
    if payment.customer_id != shipment.customer_id:
        raise ValueError("付款和发货必须属于同一客户。")
    allocated = session.scalar(
        select(func.coalesce(func.sum(PaymentAllocation.allocated_amount_cents), 0)).where(
            PaymentAllocation.payment_id == payment.id,
            PaymentAllocation.active.is_(True),
        )
    )
    if int(allocated or 0) + amount_cents > payment.amount_cents:
        raise ValueError("分配金额不能超过付款金额。")


def create_payment_allocation(session, payment: Payment, shipment: Shipment, amount_cents: int):
    validate_payment_allocation(session, payment, shipment, amount_cents)
    allocation = PaymentAllocation(
        payment=payment,
        shipment=shipment,
        allocated_amount_cents=amount_cents,
    )
    session.add(allocation)
    session.flush()
    return allocation
