"""Transactional bookkeeping workflows and audit-aware lifecycle operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from .calculation_service import (
    calculate_receivable,
    calculate_shipment,
    create_payment_allocation,
)
from .models import (
    AuditEvent,
    Customer,
    Payment,
    PaymentAllocation,
    Shipment,
    SubmissionRecord,
)
from .validation import (
    ValidationError,
    ensure_integer_units,
    validate_payment_method,
    validate_submission_token,
)


class BookkeepingError(ValueError):
    """A safe, user-facing bookkeeping rule failure."""


@dataclass(frozen=True)
class ShipmentInput:
    customer_id: int
    shipment_date: date
    total_amount_cents: int
    freight_cents: int
    unloading_fee_cents: int
    returned_pallet_tonnage_hundredths: int
    returned_pallet_amount_cents: int
    issue_deduction_cents: int
    area_hundredths: int
    rounding_cents: int
    description: str


@dataclass(frozen=True)
class PaymentInput:
    customer_id: int
    payment_date: date
    amount_cents: int
    payment_method: str
    description: str


@dataclass(frozen=True)
class AllocationInput:
    shipment_id: int
    amount_cents: int


@dataclass(frozen=True)
class RetailInput:
    retail_date: date
    location_description: str
    area_hundredths: int
    amount_cents: int
    received: bool
    payment_method: str
    payment_description: str


def _begin(session):
    # A read in the route may have opened an implicit transaction. The service owns
    # the write transaction and must not leave unrelated pending work around.
    if session().in_transaction():
        session.rollback()
    return session.begin()


def _summary(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _shipment_summary(shipment: Shipment) -> str:
    return _summary(
        {
            "id": shipment.id,
            "customer_id": shipment.customer_id,
            "shipment_date": shipment.shipment_date.isoformat(),
            "total_amount_cents": shipment.total_amount_cents,
            "active": shipment.active,
        }
    )


def _payment_summary(payment: Payment) -> str:
    return _summary(
        {
            "id": payment.id,
            "customer_id": payment.customer_id,
            "payment_date": payment.payment_date.isoformat(),
            "amount_cents": payment.amount_cents,
            "payment_method": payment.payment_method,
            "active": payment.active,
        }
    )


def _allocation_summary(allocation: PaymentAllocation) -> str:
    return _summary(
        {
            "id": allocation.id,
            "payment_id": allocation.payment_id,
            "shipment_id": allocation.shipment_id,
            "allocated_amount_cents": allocation.allocated_amount_cents,
            "active": allocation.active,
        }
    )


def _audit(session, object_type: str, object_id: int, action: str, before: str, after: str):
    session.add(
        AuditEvent(
            object_type=object_type,
            object_id=str(object_id),
            action=action,
            before_summary=before,
            after_summary=after,
        )
    )


def _submission(session, token: str, operation: str) -> SubmissionRecord | None:
    validate_submission_token(token)
    record = session.scalar(select(SubmissionRecord).where(SubmissionRecord.token == token))
    if record is not None and record.operation != operation:
        raise BookkeepingError("该提交令牌已经用于其他操作，请刷新页面后重试。")
    return record


def _save_submission(
    session, token: str, operation: str, result_type: str, result_id: int
) -> None:
    session.add(
        SubmissionRecord(
            token=token,
            operation=operation,
            result_type=result_type,
            result_id=result_id,
        )
    )
    session.flush()


def _validate_shipment_input(session, data: ShipmentInput, *, allow_archived: bool = False):
    customer = session.get(Customer, data.customer_id)
    if customer is None:
        raise BookkeepingError("客户不存在。")
    if not customer.active and not allow_archived:
        raise BookkeepingError("归档客户不能新增账务，请先恢复客户。")
    if not isinstance(data.shipment_date, date):
        raise ValidationError("日期格式不正确。")
    for field_name in (
        "total_amount_cents",
        "freight_cents",
        "unloading_fee_cents",
        "returned_pallet_tonnage_hundredths",
        "returned_pallet_amount_cents",
        "issue_deduction_cents",
        "area_hundredths",
        "rounding_cents",
    ):
        ensure_integer_units(getattr(data, field_name), field_name)
    if not isinstance(data.description, str):
        raise ValidationError("内部说明格式不正确。")
    return customer


def _validate_payment_input(session, data: PaymentInput):
    customer = session.get(Customer, data.customer_id)
    if customer is None:
        raise BookkeepingError("客户不存在。")
    if not customer.active:
        raise BookkeepingError("归档客户不能新增收款，请先恢复客户。")
    if not isinstance(data.payment_date, date):
        raise ValidationError("日期格式不正确。")
    ensure_integer_units(data.amount_cents, "收款金额", positive=True)
    validate_payment_method(data.payment_method)
    if not isinstance(data.description, str):
        raise ValidationError("收款说明格式不正确。")
    return customer


def _new_shipment(session, data: ShipmentInput) -> Shipment:
    shipment = Shipment(
        customer_id=data.customer_id,
        shipment_date=data.shipment_date,
        total_amount_cents=data.total_amount_cents,
        freight_cents=data.freight_cents,
        unloading_fee_cents=data.unloading_fee_cents,
        returned_pallet_tonnage_hundredths=data.returned_pallet_tonnage_hundredths,
        returned_pallet_amount_cents=data.returned_pallet_amount_cents,
        issue_deduction_cents=data.issue_deduction_cents,
        area_hundredths=data.area_hundredths,
        rounding_cents=data.rounding_cents,
        description=data.description,
    )
    session.add(shipment)
    session.flush()
    _audit(session, "shipment", shipment.id, "created", "", _shipment_summary(shipment))
    return shipment


def _new_payment(session, data: PaymentInput) -> Payment:
    payment = Payment(
        customer_id=data.customer_id,
        payment_date=data.payment_date,
        amount_cents=data.amount_cents,
        payment_method=data.payment_method,
        description=data.description,
    )
    session.add(payment)
    session.flush()
    _audit(session, "payment", payment.id, "created", "", _payment_summary(payment))
    return payment


def _add_allocation(session, payment: Payment, shipment: Shipment, amount_cents: int):
    try:
        allocation = create_payment_allocation(session, payment, shipment, amount_cents)
    except ValueError as exc:
        raise BookkeepingError(str(exc)) from exc
    _audit(
        session,
        "payment_allocation",
        allocation.id,
        "created",
        "",
        _allocation_summary(allocation),
    )
    return allocation


def create_shipment_with_initial_payment(
    session,
    data: ShipmentInput,
    initial_received_cents: int,
    payment_method: str,
    payment_description: str,
    submission_token: str,
) -> Shipment:
    ensure_integer_units(initial_received_cents, "初始实收款")
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "create_shipment")
        if existing is not None:
            result_id = existing.result_id
        else:
            _validate_shipment_input(session, data)
            if not isinstance(payment_description, str):
                raise ValidationError("收款说明格式不正确。")
            shipment = _new_shipment(session, data)
            if initial_received_cents > 0:
                validate_payment_method(payment_method)
                payment = _new_payment(
                    session,
                    PaymentInput(
                        customer_id=data.customer_id,
                        payment_date=data.shipment_date,
                        amount_cents=initial_received_cents,
                        payment_method=payment_method,
                        description=payment_description,
                    ),
                )
                current_due = calculate_receivable(shipment) - shipment.rounding_cents
                allocation_amount = (
                    min(initial_received_cents, current_due) if current_due > 0 else 0
                )
                if allocation_amount > 0:
                    _add_allocation(session, payment, shipment, allocation_amount)
            _save_submission(session, submission_token, "create_shipment", "shipment", shipment.id)
            result_id = shipment.id
    return session.get(Shipment, result_id)


def create_payment_workflow(
    session,
    data: PaymentInput,
    allocation_mode: str,
    allocations: list[AllocationInput],
    submission_token: str,
) -> Payment:
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "create_payment")
        if existing is not None:
            result_id = existing.result_id
        else:
            _validate_payment_input(session, data)
            if allocation_mode not in {"none", "auto", "specified"}:
                raise BookkeepingError("收款分配方式无效。")
            payment = _new_payment(session, data)
            if allocation_mode == "specified":
                for item in allocations:
                    shipment = session.get(Shipment, item.shipment_id)
                    if shipment is None:
                        raise BookkeepingError("指定的发货不存在。")
                    _add_allocation(session, payment, shipment, item.amount_cents)
            elif allocation_mode == "auto":
                remaining = payment.amount_cents
                shipments = session.scalars(
                    select(Shipment)
                    .where(
                        Shipment.customer_id == payment.customer_id,
                        Shipment.active.is_(True),
                    )
                    .order_by(Shipment.shipment_date.asc(), Shipment.id.asc())
                ).all()
                for shipment in shipments:
                    if remaining <= 0:
                        break
                    due = calculate_shipment(session, shipment).balance_cents
                    if due <= 0:
                        continue
                    amount = min(remaining, due)
                    _add_allocation(session, payment, shipment, amount)
                    remaining -= amount
            _save_submission(session, submission_token, "create_payment", "payment", payment.id)
            result_id = payment.id
    return session.get(Payment, result_id)


def allocate_existing_payment(
    session,
    payment_id: int,
    allocations: list[AllocationInput],
    submission_token: str,
) -> Payment:
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "allocate_payment")
        if existing is not None:
            result_id = existing.result_id
        else:
            payment = session.get(Payment, payment_id)
            if payment is None:
                raise BookkeepingError("收款不存在。")
            if not payment.active:
                raise BookkeepingError("已作废的收款不能分配。")
            if not allocations:
                raise BookkeepingError("请至少填写一笔分配。")
            for item in allocations:
                shipment = session.get(Shipment, item.shipment_id)
                if shipment is None:
                    raise BookkeepingError("指定的发货不存在。")
                _add_allocation(session, payment, shipment, item.amount_cents)
            _save_submission(session, submission_token, "allocate_payment", "payment", payment.id)
            result_id = payment.id
    return session.get(Payment, result_id)


def update_shipment(
    session, shipment_id: int, data: ShipmentInput, submission_token: str
) -> Shipment:
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "edit_shipment")
        if existing is not None:
            result_id = existing.result_id
        else:
            shipment = session.get(Shipment, shipment_id)
            if shipment is None:
                raise BookkeepingError("发货不存在。")
            _validate_shipment_input(session, data, allow_archived=True)
            if data.customer_id != shipment.customer_id:
                has_allocations = session.scalar(
                    select(PaymentAllocation.id)
                    .where(PaymentAllocation.shipment_id == shipment.id)
                    .limit(1)
                )
                if has_allocations is not None:
                    raise BookkeepingError("已有账务关系的发货不能更换客户。")
            before = _shipment_summary(shipment)
            shipment.customer_id = data.customer_id
            shipment.shipment_date = data.shipment_date
            shipment.total_amount_cents = data.total_amount_cents
            shipment.freight_cents = data.freight_cents
            shipment.unloading_fee_cents = data.unloading_fee_cents
            shipment.returned_pallet_tonnage_hundredths = (
                data.returned_pallet_tonnage_hundredths
            )
            shipment.returned_pallet_amount_cents = data.returned_pallet_amount_cents
            shipment.issue_deduction_cents = data.issue_deduction_cents
            shipment.area_hundredths = data.area_hundredths
            shipment.rounding_cents = data.rounding_cents
            shipment.description = data.description
            session.flush()
            _audit(session, "shipment", shipment.id, "updated", before, _shipment_summary(shipment))
            _save_submission(session, submission_token, "edit_shipment", "shipment", shipment.id)
            result_id = shipment.id
    return session.get(Shipment, result_id)


def void_shipment(session, shipment_id: int, submission_token: str) -> Shipment:
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "void_shipment")
        if existing is not None:
            result_id = existing.result_id
        else:
            shipment = session.get(Shipment, shipment_id)
            if shipment is None:
                raise BookkeepingError("发货不存在。")
            before = _shipment_summary(shipment)
            if shipment.active:
                for allocation in list(shipment.allocations):
                    if allocation.active:
                        allocation_before = _allocation_summary(allocation)
                        allocation.active = False
                        session.flush()
                        _audit(
                            session,
                            "payment_allocation",
                            allocation.id,
                            "voided_with_shipment",
                            allocation_before,
                            _allocation_summary(allocation),
                        )
                shipment.active = False
                session.flush()
                _audit(
                    session, "shipment", shipment.id, "voided", before, _shipment_summary(shipment)
                )
            _save_submission(session, submission_token, "void_shipment", "shipment", shipment.id)
            result_id = shipment.id
    return session.get(Shipment, result_id)


def void_payment(session, payment_id: int, submission_token: str) -> Payment:
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "void_payment")
        if existing is not None:
            result_id = existing.result_id
        else:
            payment = session.get(Payment, payment_id)
            if payment is None:
                raise BookkeepingError("收款不存在。")
            before = _payment_summary(payment)
            if payment.active:
                for allocation in list(payment.allocations):
                    if allocation.active:
                        allocation_before = _allocation_summary(allocation)
                        allocation.active = False
                        session.flush()
                        _audit(
                            session,
                            "payment_allocation",
                            allocation.id,
                            "voided_with_payment",
                            allocation_before,
                            _allocation_summary(allocation),
                        )
                payment.active = False
                session.flush()
                _audit(session, "payment", payment.id, "voided", before, _payment_summary(payment))
            _save_submission(session, submission_token, "void_payment", "payment", payment.id)
            result_id = payment.id
    return session.get(Payment, result_id)


def revoke_allocation(
    session, allocation_id: int, submission_token: str
) -> PaymentAllocation:
    validate_submission_token(submission_token)
    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "revoke_allocation")
        if existing is not None:
            result_id = existing.result_id
        else:
            allocation = session.get(PaymentAllocation, allocation_id)
            if allocation is None:
                raise BookkeepingError("分配记录不存在。")
            before = _allocation_summary(allocation)
            if allocation.active:
                allocation.active = False
                session.flush()
                _audit(
                    session,
                    "payment_allocation",
                    allocation.id,
                    "revoked",
                    before,
                    _allocation_summary(allocation),
                )
            _save_submission(
                session, submission_token, "revoke_allocation", "allocation", allocation.id
            )
            result_id = allocation.id
    return session.get(PaymentAllocation, result_id)


def create_retail_workflow(
    session, data: RetailInput, submission_token: str
) -> Shipment:
    validate_submission_token(submission_token)
    ensure_integer_units(data.area_hundredths, "平方数")
    ensure_integer_units(data.amount_cents, "金额")
    if not isinstance(data.retail_date, date):
        raise ValidationError("日期格式不正确。")
    if not isinstance(data.location_description, str):
        raise ValidationError("地点或说明格式不正确。")
    if not isinstance(data.received, bool):
        raise ValidationError("是否收款格式不正确。")
    if data.received:
        ensure_integer_units(data.amount_cents, "金额", positive=True)
        validate_payment_method(data.payment_method)
        if not isinstance(data.payment_description, str):
            raise ValidationError("收款说明格式不正确。")

    result_id = None
    with _begin(session):
        existing = _submission(session, submission_token, "create_retail")
        if existing is not None:
            result_id = existing.result_id
        else:
            customer = session.scalar(
                select(Customer).where(Customer.normalized_name == "厂里零售")
            )
            if customer is None:
                customer = Customer(
                    name="厂里零售", normalized_name="厂里零售", notes="厂里零售快捷入口"
                )
                session.add(customer)
                session.flush()
                _audit(
                    session,
                    "customer",
                    customer.id,
                    "created_for_retail",
                    "",
                    _summary({"name": customer.name, "active": customer.active}),
                )
            elif not customer.active:
                before = _summary({"name": customer.name, "active": customer.active})
                customer.active = True
                session.flush()
                _audit(
                    session,
                    "customer",
                    customer.id,
                    "restored_for_retail",
                    before,
                    _summary({"name": customer.name, "active": customer.active}),
                )
            shipment = _new_shipment(
                session,
                ShipmentInput(
                    customer_id=customer.id,
                    shipment_date=data.retail_date,
                    total_amount_cents=data.amount_cents,
                    freight_cents=0,
                    unloading_fee_cents=0,
                    returned_pallet_tonnage_hundredths=0,
                    returned_pallet_amount_cents=0,
                    issue_deduction_cents=0,
                    area_hundredths=data.area_hundredths,
                    rounding_cents=0,
                    description=data.location_description,
                ),
            )
            if data.received:
                payment = _new_payment(
                    session,
                    PaymentInput(
                        customer_id=customer.id,
                        payment_date=data.retail_date,
                        amount_cents=data.amount_cents,
                        payment_method=data.payment_method,
                        description=data.payment_description,
                    ),
                )
                _add_allocation(session, payment, shipment, data.amount_cents)
            _save_submission(session, submission_token, "create_retail", "shipment", shipment.id)
            result_id = shipment.id
    return session.get(Shipment, result_id)
