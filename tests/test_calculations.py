from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import StatementError

from customer_ledger.calculation_service import (
    calculate_receivable,
    calculate_shipment,
    customer_net_balance,
    customer_total_received,
)
from customer_ledger.extensions import db
from customer_ledger.models import Customer, Payment, Shipment


def test_one_cent_round_trip_uses_integer_cents(app):
    shipment = Shipment(
        shipment_date=date(2026, 1, 1),
        total_amount_cents=1,
        freight_cents=0,
        unloading_fee_cents=0,
        returned_pallet_tonnage_hundredths=0,
        returned_pallet_amount_cents=0,
        issue_deduction_cents=0,
        area_hundredths=0,
        rounding_cents=0,
        description="synthetic",
    )

    assert calculate_receivable(shipment) == 1
    assert Decimal(calculate_receivable(shipment)) / Decimal(100) == Decimal("0.01")


def test_money_and_quantity_fields_reject_float_values(app):
    with app.app_context():
        shipment = Shipment(total_amount_cents=0.01)
        db.session.add(shipment)
        with pytest.raises(StatementError, match="整数"):
            db.session.flush()
        db.session.rollback()


def test_receivable_formula_and_non_money_quantities(app):
    shipment = Shipment(
        shipment_date=date(2026, 1, 1),
        total_amount_cents=1_000_000,
        freight_cents=30_000,
        unloading_fee_cents=10_000,
        returned_pallet_tonnage_hundredths=250,
        returned_pallet_amount_cents=120_000,
        issue_deduction_cents=0,
        area_hundredths=52_080,
        rounding_cents=0,
        description="synthetic",
    )

    assert calculate_receivable(shipment) == 840_000
    assert shipment.returned_pallet_tonnage_hundredths == 250
    assert shipment.area_hundredths == 52_080


def test_negative_receivable_is_not_clamped(app):
    shipment = Shipment(
        total_amount_cents=100,
        freight_cents=200,
        unloading_fee_cents=0,
        returned_pallet_tonnage_hundredths=0,
        returned_pallet_amount_cents=0,
        issue_deduction_cents=0,
        area_hundredths=0,
        rounding_cents=0,
        description="synthetic",
    )
    assert calculate_receivable(shipment) == -100


def test_customer_totals_use_only_effective_records(app):
    with app.app_context():
        customer = Customer(name="测试客户", normalized_name="测试客户")
        db.session.add(customer)
        db.session.flush()
        db.session.add_all(
            [
                Shipment(
                    customer_id=customer.id,
                    total_amount_cents=1000,
                    freight_cents=0,
                    unloading_fee_cents=0,
                    returned_pallet_tonnage_hundredths=0,
                    returned_pallet_amount_cents=0,
                    issue_deduction_cents=0,
                    area_hundredths=0,
                    rounding_cents=50,
                    description="valid",
                    active=True,
                ),
                Shipment(
                    customer_id=customer.id,
                    total_amount_cents=9000,
                    freight_cents=0,
                    unloading_fee_cents=0,
                    returned_pallet_tonnage_hundredths=0,
                    returned_pallet_amount_cents=0,
                    issue_deduction_cents=0,
                    area_hundredths=0,
                    rounding_cents=0,
                    description="void",
                    active=False,
                ),
            ]
        )
        db.session.add_all(
            [
                Payment(customer_id=customer.id, amount_cents=300, payment_method="转账"),
                Payment(
                    customer_id=customer.id, amount_cents=500, payment_method="现金", active=False
                ),
            ]
        )
        db.session.commit()

        assert customer_total_received(db.session, customer) == 300
        assert customer_net_balance(db.session, customer) == 650


def test_shipment_balance_subtracts_active_allocations_and_rounding(app):
    with app.app_context():
        customer = Customer(name="余额客户", normalized_name="余额客户")
        db.session.add(customer)
        db.session.flush()
        shipment = Shipment(
            customer_id=customer.id,
            total_amount_cents=1000,
            freight_cents=0,
            unloading_fee_cents=0,
            returned_pallet_tonnage_hundredths=0,
            returned_pallet_amount_cents=0,
            issue_deduction_cents=0,
            area_hundredths=0,
            rounding_cents=10,
            description="synthetic",
        )
        db.session.add(shipment)
        db.session.commit()
        assert calculate_shipment(db.session, shipment).balance_cents == 990
