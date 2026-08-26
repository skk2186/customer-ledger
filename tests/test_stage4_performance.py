from __future__ import annotations

import json
import time
from datetime import date

from customer_ledger.backup_service import create_backup, validate_backup
from customer_ledger.extensions import db
from customer_ledger.models import Customer, Payment, PaymentAllocation, Shipment


def test_anonymous_stage4_scale_performance(client, app, tmp_path):
    timings = {}
    started = time.perf_counter()
    with app.app_context():
        customers = [
            Customer(
                name=f"性能合成客户{index:03d}",
                normalized_name=f"性能合成客户{index:03d}".casefold(),
                notes="",
            )
            for index in range(100)
        ]
        db.session.add_all(customers)
        db.session.flush()
        shipments = []
        for customer in customers:
            shipments.extend(
                Shipment(
                    customer_id=customer.id,
                    shipment_date=date(2026, 8, 1),
                    total_amount_cents=100_000,
                    freight_cents=0,
                    unloading_fee_cents=0,
                    returned_pallet_tonnage_hundredths=0,
                    returned_pallet_amount_cents=0,
                    issue_deduction_cents=0,
                    area_hundredths=0,
                    rounding_cents=0,
                    description="",
                )
                for _ in range(100)
            )
        db.session.add_all(shipments)
        db.session.flush()
        payments = [
            Payment(
                customer_id=customer.id,
                payment_date=date(2026, 8, 2),
                amount_cents=1_000,
                payment_method="银行转账",
                description="",
            )
            for customer in customers
        ]
        db.session.add_all(payments)
        db.session.flush()
        allocations = [
            PaymentAllocation(
                payment_id=payments[customer_index].id,
                shipment_id=shipments[customer_index * 100 + shipment_index * 10].id,
                allocated_amount_cents=100,
            )
            for customer_index in range(100)
            for shipment_index in range(10)
        ]
        db.session.add_all(allocations)
        db.session.commit()
        timings["database_prep_seconds"] = round(time.perf_counter() - started, 3)

        started = time.perf_counter()
        customer_list = client.get("/customers")
        timings["customer_list_seconds"] = round(time.perf_counter() - started, 3)
        assert customer_list.status_code == 200

        started = time.perf_counter()
        ledger = client.get(f"/customers/{customers[0].id}/ledger")
        timings["single_ledger_seconds"] = round(time.perf_counter() - started, 3)
        assert ledger.status_code == 200

        started = time.perf_counter()
        summary = client.get("/summary")
        timings["summary_seconds"] = round(time.perf_counter() - started, 3)
        assert summary.status_code == 200

        started = time.perf_counter()
        all_ledgers = client.get("/exports/all-ledgers.xlsx")
        timings["all_ledgers_xlsx_seconds"] = round(time.perf_counter() - started, 3)
        assert all_ledgers.status_code == 200

        started = time.perf_counter()
        manifest = create_backup(
            db.engine,
            backup_dir=tmp_path / "performance-backups",
            reason="performance",
        )
        timings["backup_seconds"] = round(time.perf_counter() - started, 3)

        started = time.perf_counter()
        validate_backup(tmp_path / "performance-backups" / manifest.manifest_filename)
        timings["integrity_seconds"] = round(time.perf_counter() - started, 3)

    print("STAGE4_PERFORMANCE " + json.dumps(timings, ensure_ascii=False, sort_keys=True))
    assert timings["database_prep_seconds"] < 30
    assert timings["customer_list_seconds"] < 10
    assert timings["single_ledger_seconds"] < 10
    assert timings["summary_seconds"] < 30
    assert timings["all_ledgers_xlsx_seconds"] < 90
    assert timings["backup_seconds"] < 30
    assert timings["integrity_seconds"] < 30
