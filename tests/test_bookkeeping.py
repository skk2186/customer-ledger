from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from customer_ledger.bookkeeping_service import (
    AllocationInput,
    BookkeepingError,
    PaymentInput,
    RetailInput,
    ShipmentInput,
    allocate_existing_payment,
    create_payment_workflow,
    create_retail_workflow,
    create_shipment_with_initial_payment,
    revoke_allocation,
    update_shipment,
    void_payment,
    void_shipment,
)
from customer_ledger.calculation_service import (
    calculate_shipment,
    customer_summary,
    payment_unallocated_cents,
    summarize_customers,
)
from customer_ledger.customer_service import archive_customer, create_customer
from customer_ledger.extensions import db
from customer_ledger.models import Customer, Payment, PaymentAllocation, Shipment
from customer_ledger.validation import (
    ValidationError,
    parse_money_cents,
    parse_quantity_hundredths,
)


def _customer(name: str) -> Customer:
    customer = create_customer(db.session, name)
    db.session.commit()
    return customer


def _shipment_input(customer_id: int, shipment_date: date = date(2026, 1, 1), **changes):
    values = {
        "customer_id": customer_id,
        "shipment_date": shipment_date,
        "total_amount_cents": 100_000,
        "freight_cents": 0,
        "unloading_fee_cents": 0,
        "returned_pallet_tonnage_hundredths": 0,
        "returned_pallet_amount_cents": 0,
        "issue_deduction_cents": 0,
        "area_hundredths": 0,
        "rounding_cents": 0,
        "description": "合成测试",
    }
    values.update(changes)
    return ShipmentInput(**values)


def _payment_input(customer_id: int, amount_cents: int, payment_date=date(2026, 1, 2)):
    return PaymentInput(
        customer_id=customer_id,
        payment_date=payment_date,
        amount_cents=amount_cents,
        payment_method="银行转账",
        description="合成收款",
    )


def test_decimal_parser_rejects_float_like_and_invalid_input():
    assert parse_money_cents("0.01", "金额") == 1
    assert parse_quantity_hundredths("2.50", "吨位") == 250
    assert parse_money_cents("", "金额") == 0
    for value in ("1.234", "1e2", "NaN", "Infinity", "-1", "abc"):
        with pytest.raises(ValidationError):
            parse_money_cents(value, "金额")
    assert Decimal(parse_money_cents("12.34", "金额")) / Decimal(100) == Decimal("12.34")


def test_shipment_initial_payment_formula_and_no_zero_payment(app):
    with app.app_context():
        customer = _customer("记账客户")
        shipment = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(
                customer.id,
                total_amount_cents=1_000_000,
                freight_cents=30_000,
                unloading_fee_cents=10_000,
                returned_pallet_tonnage_hundredths=250,
                returned_pallet_amount_cents=120_000,
                area_hundredths=52_080,
            ),
            800_000,
            "银行转账",
            "首款",
            "token-shipment-01",
        )
        assert calculate_shipment(db.session, shipment).receivable_cents == 840_000
        assert calculate_shipment(db.session, shipment).received_cents == 800_000
        assert calculate_shipment(db.session, shipment).balance_cents == 40_000
        assert db.session.scalar(select(func.count(Payment.id))) == 1

        unpaid = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id, shipment_date=date(2026, 1, 3)),
            0,
            "银行转账",
            "",
            "token-shipment-02",
        )
        assert unpaid.id != shipment.id
        assert db.session.scalar(select(func.count(Payment.id))) == 1


def test_overpayment_is_preserved_as_unallocated_prepayment(app):
    with app.app_context():
        customer = _customer("预收客户")
        shipment = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id, rounding_cents=100),
            120_000,
            "现金",
            "超额收款",
            "token-overpay-01",
        )
        summary = customer_summary(db.session, customer.id)
        assert calculate_shipment(db.session, shipment).received_cents == 99_900
        assert summary.total_received_cents == 120_000
        assert summary.unallocated_prepayment_cents == 20_100
        assert summary.net_balance_cents == -20_100


def test_one_payment_can_be_split_and_reallocation_does_not_change_net_balance(app):
    with app.app_context():
        customer = _customer("分配客户")
        first = create_shipment_with_initial_payment(
            db.session, _shipment_input(customer.id), 0, "银行转账", "", "token-split-s1"
        )
        second = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id, shipment_date=date(2026, 1, 2)),
            0,
            "银行转账",
            "",
            "token-split-s2",
        )
        payment = create_payment_workflow(
            db.session,
            _payment_input(customer.id, 150_000),
            "specified",
            [AllocationInput(first.id, 60_000), AllocationInput(second.id, 90_000)],
            "token-split-payment",
        )
        assert len(payment.allocations) == 2
        before = customer_summary(db.session, customer.id).net_balance_cents
        assert before == 50_000
        allocation = payment.allocations[0]
        revoke_allocation(db.session, allocation.id, "token-split-revoke")
        after = customer_summary(db.session, customer.id).net_balance_cents
        assert after == before
        assert payment_unallocated_cents(db.session, payment) == 60_000


def test_auto_allocation_is_stable_and_partial(app):
    with app.app_context():
        customer = _customer("自动分配客户")
        first = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id, shipment_date=date(2026, 1, 1), total_amount_cents=50_000),
            0,
            "银行转账",
            "",
            "token-auto-s1",
        )
        second = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id, shipment_date=date(2026, 1, 2), total_amount_cents=80_000),
            0,
            "银行转账",
            "",
            "token-auto-s2",
        )
        payment = create_payment_workflow(
            db.session,
            _payment_input(customer.id, 70_000),
            "auto",
            [],
            "token-auto-payment",
        )
        assert calculate_shipment(db.session, first).received_cents == 50_000
        assert calculate_shipment(db.session, second).received_cents == 20_000
        assert payment_unallocated_cents(db.session, payment) == 0


def test_multiple_receipts_and_payment_before_later_shipment(app):
    with app.app_context():
        customer = _customer("多次收款客户")
        first_payment = create_payment_workflow(
            db.session,
            _payment_input(customer.id, 30_000, date(2026, 1, 1)),
            "none",
            [],
            "token-multi-payment-one",
        )
        shipment = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id, shipment_date=date(2026, 1, 2)),
            0,
            "现金",
            "",
            "token-multi-shipment",
        )
        allocate_existing_payment(
            db.session,
            first_payment.id,
            [AllocationInput(shipment.id, 30_000)],
            "token-multi-allocate-first",
        )
        second_payment = create_payment_workflow(
            db.session,
            _payment_input(customer.id, 20_000, date(2026, 1, 3)),
            "specified",
            [AllocationInput(shipment.id, 20_000)],
            "token-multi-payment-two",
        )
        assert second_payment.id != first_payment.id
        assert calculate_shipment(db.session, shipment).received_cents == 50_000
        assert payment_unallocated_cents(db.session, first_payment) == 0


def test_cross_customer_allocation_rolls_back_payment_creation(app):
    with app.app_context():
        first = _customer("事务甲")
        second = _customer("事务乙")
        shipment = create_shipment_with_initial_payment(
            db.session, _shipment_input(second.id), 0, "银行转账", "", "token-tx-s1"
        )
        with pytest.raises(BookkeepingError, match="同一客户"):
            create_payment_workflow(
                db.session,
                _payment_input(first.id, 30_000),
                "specified",
                [AllocationInput(shipment.id, 10_000)],
                "token-tx-payment",
            )
        assert db.session.scalar(
            select(func.count(Payment.id)).where(Payment.customer_id == first.id)
        ) == 0


def test_edit_and_void_records_keep_audit_and_restore_prepayment(app):
    with app.app_context():
        first = _customer("编辑客户")
        second = _customer("不可换客户")
        shipment = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(first.id),
            20_000,
            "微信",
            "初始",
            "token-edit-s1",
        )
        updated = update_shipment(
            db.session,
            shipment.id,
            _shipment_input(first.id, total_amount_cents=120_000),
            "token-edit-update",
        )
        assert updated.total_amount_cents == 120_000
        with pytest.raises(BookkeepingError, match="不能更换客户"):
            update_shipment(
                db.session,
                shipment.id,
                _shipment_input(second.id),
                "token-edit-customer",
            )
        payment = db.session.scalar(select(Payment).where(Payment.customer_id == first.id))
        void_shipment(db.session, shipment.id, "token-edit-void-shipment")
        assert db.session.get(Shipment, shipment.id).active is False
        assert db.session.get(Payment, payment.id).active is True
        assert payment_unallocated_cents(db.session, payment) == payment.amount_cents

        other = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(first.id, shipment_date=date(2026, 2, 1)),
            0,
            "微信",
            "",
            "token-edit-s2",
        )
        payment_two = create_payment_workflow(
            db.session,
            _payment_input(first.id, 10_000, date(2026, 2, 2)),
            "specified",
            [AllocationInput(other.id, 10_000)],
            "token-edit-payment-two",
        )
        void_payment(db.session, payment_two.id, "token-edit-void-payment")
        assert db.session.get(Payment, payment_two.id).active is False
        assert db.session.scalar(
            select(func.count(PaymentAllocation.id)).where(
                PaymentAllocation.payment_id == payment_two.id,
                PaymentAllocation.active.is_(True),
            )
        ) == 0


def test_duplicate_submission_token_creates_one_workflow(app):
    with app.app_context():
        customer = _customer("幂等客户")
        data = _shipment_input(customer.id)
        first = create_shipment_with_initial_payment(
            db.session, data, 10_000, "支付宝", "", "token-idempotent"
        )
        second = create_shipment_with_initial_payment(
            db.session, data, 10_000, "支付宝", "", "token-idempotent"
        )
        assert first.id == second.id
        assert db.session.scalar(
            select(func.count(Shipment.id)).where(Shipment.customer_id == customer.id)
        ) == 1
        assert db.session.scalar(
            select(func.count(Payment.id)).where(Payment.customer_id == customer.id)
        ) == 1


def test_retail_mapping_and_unpaid_does_not_create_zero_payment(app):
    with app.app_context():
        unpaid = create_retail_workflow(
            db.session,
            RetailInput(date(2026, 3, 1), "门口", 52080, 30_000, False, "现金", ""),
            "token-retail-unpaid",
        )
        customer = db.session.get(Customer, unpaid.customer_id)
        assert customer.name == "厂里零售"
        assert unpaid.total_amount_cents == 30_000
        assert unpaid.area_hundredths == 52_080
        assert db.session.scalar(
            select(func.count(Payment.id)).where(Payment.customer_id == customer.id)
        ) == 0

        paid = create_retail_workflow(
            db.session,
            RetailInput(date(2026, 3, 2), "仓库", 100, 5_000, True, "现金", "已收"),
            "token-retail-paid",
        )
        payment = db.session.scalar(
            select(Payment).where(Payment.customer_id == customer.id, Payment.amount_cents == 5_000)
        )
        assert paid.customer_id == payment.customer_id
        assert calculate_shipment(db.session, paid).received_cents == 5_000


def test_summary_cutoff_archived_customer_and_read_only_http(client, app):
    with app.app_context():
        customer = _customer("历史汇总客户")
        create_shipment_with_initial_payment(
            db.session,
            _shipment_input(
                customer.id, shipment_date=date(2026, 1, 1), total_amount_cents=100_000
            ),
            0,
            "现金",
            "",
            "token-summary-s1",
        )
        create_payment_workflow(
            db.session,
            _payment_input(customer.id, 30_000, date(2026, 1, 2)),
            "none",
            [],
            "token-summary-p1",
        )
        create_shipment_with_initial_payment(
            db.session,
            _shipment_input(
                customer.id, shipment_date=date(2026, 2, 1), total_amount_cents=200_000
            ),
            0,
            "现金",
            "",
            "token-summary-s2",
        )
        archive_customer(db.session, customer)
        db.session.commit()
        rows, total = summarize_customers(db.session, date(2026, 1, 31))
        row = next(item for item in rows if item.customer.id == customer.id)
        assert row.customer.active is False
        assert row.summary.total_goods_cents == 100_000
        assert row.summary.total_received_cents == 30_000
        assert row.summary.total_receivable_cents == (
            row.summary.total_received_cents
            + row.summary.net_balance_cents
            + row.summary.total_rounding_cents
        )
        assert total.total_goods_cents >= row.summary.total_goods_cents

    response = client.post("/summary")
    assert response.status_code == 405
    assert client.get("/summary?as_of=2026-01-31").status_code == 200


def test_http_new_shipment_and_payment_pages(client, app):
    response = client.post("/customers/new", data={"name": "网页记账客户", "notes": ""})
    assert response.status_code == 302
    with app.app_context():
        customer = db.session.scalar(select(Customer).where(Customer.name == "网页记账客户"))
        customer_id = customer.id

    response = client.post(
        "/shipments/new",
        data={
            "submission_token": "token-http-shipment",
            "customer_id": str(customer_id),
            "shipment_date": "2026-04-01",
            "total_amount": "100.00",
            "freight": "10.00",
            "unloading_fee": "",
            "returned_pallet_tonnage": "",
            "returned_pallet_amount": "",
            "issue_deduction": "",
            "area": "2.50",
            "rounding": "",
            "description": "网页发货",
            "initial_received": "20.00",
            "payment_method": "银行转账",
            "payment_description": "网页首款",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        shipment = db.session.scalar(select(Shipment).where(Shipment.customer_id == customer_id))
        shipment_id = shipment.id
    response = client.post(
        "/payments/new",
        data={
            "submission_token": "token-http-payment",
            "customer_id": str(customer_id),
            "payment_date": "2026-04-02",
            "amount": "30.00",
            "payment_method": "微信",
            "description": "网页二次收款",
            "allocation_mode": "specified",
            "shipment_id": str(shipment_id),
            "allocation_amount": "30.00",
        },
    )
    assert response.status_code == 302
    ledger = client.get(f"/customers/{customer_id}/ledger")
    assert ledger.status_code == 200
    assert "发货明细" in ledger.get_data(as_text=True)
    assert client.get("/summary").status_code == 200
