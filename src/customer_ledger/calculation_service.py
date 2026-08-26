"""The single source of truth for all accounting calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select

from .models import Customer, Payment, PaymentAllocation, Shipment


@dataclass(frozen=True)
class ShipmentCalculation:
    receivable_cents: int
    received_cents: int
    balance_cents: int


@dataclass(frozen=True)
class ShipmentLedgerRow:
    shipment: Shipment
    calculation: ShipmentCalculation
    payment_methods: str


@dataclass(frozen=True)
class CustomerSummary:
    total_goods_cents: int
    total_freight_cents: int
    total_unloading_fee_cents: int
    total_tonnage_hundredths: int
    total_returned_pallet_cents: int
    total_issue_deduction_cents: int
    total_area_hundredths: int
    total_receivable_cents: int
    total_received_cents: int
    total_allocated_received_cents: int
    total_shipment_balance_cents: int
    total_rounding_cents: int
    net_balance_cents: int
    unallocated_prepayment_cents: int


@dataclass(frozen=True)
class CustomerSummaryRow:
    customer: Customer
    summary: CustomerSummary


def calculate_receivable(shipment: Shipment) -> int:
    """Calculate receivable in cents; negative results are intentionally retained."""

    return (
        shipment.total_amount_cents
        - shipment.freight_cents
        - shipment.unloading_fee_cents
        - shipment.returned_pallet_amount_cents
        - shipment.issue_deduction_cents
    )


def effective_allocated_cents(
    session, shipment_id: int, as_of: date | None = None
) -> int:
    statement = (
        select(func.coalesce(func.sum(PaymentAllocation.allocated_amount_cents), 0))
        .select_from(PaymentAllocation)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .join(Shipment, Shipment.id == PaymentAllocation.shipment_id)
        .where(
            PaymentAllocation.shipment_id == shipment_id,
            PaymentAllocation.active.is_(True),
            Payment.active.is_(True),
            Shipment.active.is_(True),
        )
    )
    if as_of is not None:
        statement = statement.where(
            Payment.payment_date <= as_of,
            Shipment.shipment_date <= as_of,
        )
    value = session.scalar(statement)
    return int(value or 0)


def calculate_shipment(
    session, shipment: Shipment, as_of: date | None = None
) -> ShipmentCalculation:
    received = effective_allocated_cents(session, shipment.id, as_of=as_of)
    receivable = calculate_receivable(shipment)
    return ShipmentCalculation(
        receivable_cents=receivable,
        received_cents=received,
        balance_cents=receivable - received - shipment.rounding_cents,
    )


def effective_payment_cents(
    session, customer_id: int, as_of: date | None = None
) -> int:
    statement = select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
        Payment.customer_id == customer_id,
        Payment.active.is_(True),
    )
    if as_of is not None:
        statement = statement.where(Payment.payment_date <= as_of)
    value = session.scalar(statement)
    return int(value or 0)


def payment_allocated_cents(
    session, payment_id: int, as_of: date | None = None
) -> int:
    statement = (
        select(func.coalesce(func.sum(PaymentAllocation.allocated_amount_cents), 0))
        .select_from(PaymentAllocation)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .join(Shipment, Shipment.id == PaymentAllocation.shipment_id)
        .where(
            PaymentAllocation.payment_id == payment_id,
            PaymentAllocation.active.is_(True),
            Payment.active.is_(True),
            Shipment.active.is_(True),
        )
    )
    if as_of is not None:
        statement = statement.where(
            Payment.payment_date <= as_of,
            Shipment.shipment_date <= as_of,
        )
    value = session.scalar(statement)
    return int(value or 0)


def payment_unallocated_cents(
    session, payment: Payment, as_of: date | None = None
) -> int:
    if not payment.active or (as_of is not None and payment.payment_date > as_of):
        return 0
    return payment.amount_cents - payment_allocated_cents(session, payment.id, as_of=as_of)


def customer_total_received(
    session, customer: Customer, as_of: date | None = None
) -> int:
    return effective_payment_cents(session, customer.id, as_of=as_of)


def customer_summary(
    session, customer_id: int, as_of: date | None = None
) -> CustomerSummary:
    statement = select(Shipment).where(
        Shipment.customer_id == customer_id,
        Shipment.active.is_(True),
    )
    if as_of is not None:
        statement = statement.where(Shipment.shipment_date <= as_of)
    shipments = session.scalars(statement).all()

    total_goods = sum(shipment.total_amount_cents for shipment in shipments)
    total_freight = sum(shipment.freight_cents for shipment in shipments)
    total_unloading = sum(shipment.unloading_fee_cents for shipment in shipments)
    total_tonnage = sum(shipment.returned_pallet_tonnage_hundredths for shipment in shipments)
    total_returned = sum(shipment.returned_pallet_amount_cents for shipment in shipments)
    total_issue = sum(shipment.issue_deduction_cents for shipment in shipments)
    total_area = sum(shipment.area_hundredths for shipment in shipments)
    total_rounding = sum(shipment.rounding_cents for shipment in shipments)
    total_receivable = sum(calculate_receivable(shipment) for shipment in shipments)
    total_received = effective_payment_cents(session, customer_id, as_of=as_of)

    allocation_statement = (
        select(func.coalesce(func.sum(PaymentAllocation.allocated_amount_cents), 0))
        .select_from(PaymentAllocation)
        .join(Payment, Payment.id == PaymentAllocation.payment_id)
        .join(Shipment, Shipment.id == PaymentAllocation.shipment_id)
        .where(
            Payment.customer_id == customer_id,
            Payment.active.is_(True),
            PaymentAllocation.active.is_(True),
            Shipment.active.is_(True),
        )
    )
    if as_of is not None:
        allocation_statement = allocation_statement.where(
            Payment.payment_date <= as_of,
            Shipment.shipment_date <= as_of,
        )
    allocated = int(session.scalar(allocation_statement) or 0)

    return CustomerSummary(
        total_goods_cents=total_goods,
        total_freight_cents=total_freight,
        total_unloading_fee_cents=total_unloading,
        total_tonnage_hundredths=total_tonnage,
        total_returned_pallet_cents=total_returned,
        total_issue_deduction_cents=total_issue,
        total_area_hundredths=total_area,
        total_receivable_cents=total_receivable,
        total_received_cents=total_received,
        total_allocated_received_cents=allocated,
        total_shipment_balance_cents=total_receivable - allocated - total_rounding,
        total_rounding_cents=total_rounding,
        net_balance_cents=total_receivable - total_received - total_rounding,
        unallocated_prepayment_cents=total_received - allocated,
    )


def customer_net_balance(session, customer: Customer) -> int:
    return customer_summary(session, customer.id).net_balance_cents


def verify_accounting_identities(session) -> None:
    """Raise when restored active data no longer satisfies the signed formulas."""

    customers = session.scalars(select(Customer)).all()
    for customer in customers:
        summary = customer_summary(session, customer.id)
        if summary.net_balance_cents != (
            summary.total_receivable_cents
            - summary.total_received_cents
            - summary.total_rounding_cents
        ):
            raise ValueError("恢复后的客户余额校验失败。")
        if summary.total_receivable_cents != (
            summary.total_allocated_received_cents
            + summary.total_shipment_balance_cents
            + summary.total_rounding_cents
        ):
            raise ValueError("恢复后的发货合计校验失败。")


def _rollup_summaries(summaries: list[CustomerSummary]) -> CustomerSummary:
    fields = (
        "total_goods_cents",
        "total_freight_cents",
        "total_unloading_fee_cents",
        "total_tonnage_hundredths",
        "total_returned_pallet_cents",
        "total_issue_deduction_cents",
        "total_area_hundredths",
        "total_receivable_cents",
        "total_received_cents",
        "total_allocated_received_cents",
        "total_shipment_balance_cents",
        "total_rounding_cents",
        "net_balance_cents",
        "unallocated_prepayment_cents",
    )
    values = {field: sum(getattr(item, field) for item in summaries) for field in fields}
    return CustomerSummary(**values)


def summarize_customers(session, as_of: date) -> tuple[list[CustomerSummaryRow], CustomerSummary]:
    customers = session.scalars(select(Customer).order_by(Customer.name.asc())).all()
    rows = [
        CustomerSummaryRow(customer=customer, summary=customer_summary(session, customer.id, as_of))
        for customer in customers
    ]
    return rows, _rollup_summaries([row.summary for row in rows])


def customer_ledger_rows(
    session,
    customer_id: int,
    as_of: date | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[ShipmentLedgerRow]:
    statement = select(Shipment).where(Shipment.customer_id == customer_id).order_by(
        Shipment.shipment_date.asc(), Shipment.id.asc()
    )
    if as_of is not None:
        statement = statement.where(Shipment.shipment_date <= as_of)
    if limit is not None:
        statement = statement.limit(limit).offset(offset)
    shipments = session.scalars(statement).all()
    rows = []
    for shipment in shipments:
        methods_statement = (
            select(Payment.payment_method)
            .select_from(PaymentAllocation)
            .join(Payment, Payment.id == PaymentAllocation.payment_id)
            .where(
                PaymentAllocation.shipment_id == shipment.id,
                PaymentAllocation.active.is_(True),
                Payment.active.is_(True),
            )
            .distinct()
            .order_by(Payment.payment_method.asc())
        )
        if as_of is not None:
            methods_statement = methods_statement.where(Payment.payment_date <= as_of)
        methods = ", ".join(session.scalars(methods_statement).all())
        rows.append(
            ShipmentLedgerRow(
                shipment=shipment,
                calculation=calculate_shipment(session, shipment, as_of=as_of),
                payment_methods=methods,
            )
        )
    return rows


def validate_payment_allocation(
    session, payment: Payment, shipment: Shipment, amount_cents: int
) -> None:
    """Validate a new allocation before it is persisted."""

    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise ValueError("分配金额必须使用整数分。")
    if amount_cents <= 0:
        raise ValueError("分配金额必须大于 0。")
    if not payment.active or not shipment.active:
        raise ValueError("只能给有效的付款和发货建立分配。")
    if payment.customer_id != shipment.customer_id:
        raise ValueError("付款和发货必须属于同一客户。")
    if payment.amount_cents <= 0:
        raise ValueError("付款金额必须大于 0。")
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
