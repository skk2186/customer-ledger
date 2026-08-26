from customer_ledger.calculation_service import create_payment_allocation
from customer_ledger.extensions import db
from customer_ledger.models import Customer, Payment, Shipment


def _shipment(customer_id: int) -> Shipment:
    return Shipment(
        customer_id=customer_id,
        total_amount_cents=10_000,
        freight_cents=0,
        unloading_fee_cents=0,
        returned_pallet_tonnage_hundredths=0,
        returned_pallet_amount_cents=0,
        issue_deduction_cents=0,
        area_hundredths=0,
        rounding_cents=0,
        description="synthetic",
    )


def test_allocation_cannot_exceed_payment_or_cross_customer(app):
    with app.app_context():
        first = Customer(name="甲客户", normalized_name="甲客户")
        second = Customer(name="乙客户", normalized_name="乙客户")
        db.session.add_all([first, second])
        db.session.flush()
        payment = Payment(customer_id=first.id, amount_cents=1000, payment_method="转账")
        first_shipment = _shipment(first.id)
        second_shipment = _shipment(second.id)
        db.session.add_all([payment, first_shipment, second_shipment])
        db.session.commit()

        create_payment_allocation(db.session, payment, first_shipment, 1000)
        db.session.commit()

        try:
            create_payment_allocation(db.session, payment, first_shipment, 1)
            raise AssertionError("expected over-allocation to fail")
        except ValueError as exc:
            assert "超过付款" in str(exc)

        try:
            create_payment_allocation(db.session, payment, second_shipment, 1)
            raise AssertionError("expected cross-customer allocation to fail")
        except ValueError as exc:
            assert "同一客户" in str(exc)
