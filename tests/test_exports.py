from datetime import date
from decimal import Decimal

from openpyxl import load_workbook

from customer_ledger.bookkeeping_service import ShipmentInput, create_shipment_with_initial_payment
from customer_ledger.customer_service import create_customer
from customer_ledger.export_service import (
    CUSTOMER_HEADERS,
    SUMMARY_HEADERS,
    export_all_ledger_workbook,
    export_customer_workbook,
    export_summary_workbook,
)
from customer_ledger.extensions import db
from customer_ledger.models import Customer


def _customer(name: str) -> Customer:
    customer = create_customer(db.session, name)
    db.session.commit()
    return customer


def _shipment_input(customer_id: int) -> ShipmentInput:
    return ShipmentInput(
        customer_id=customer_id,
        shipment_date=date(2026, 5, 1),
        total_amount_cents=100_000,
        freight_cents=0,
        unloading_fee_cents=0,
        returned_pallet_tonnage_hundredths=0,
        returned_pallet_amount_cents=0,
        issue_deduction_cents=0,
        area_hundredths=0,
        rounding_cents=0,
        description="合成导出",
    )


def _number(value) -> Decimal:
    return Decimal(str(value))


def test_customer_export_has_allocated_and_prepayment_rows(tmp_path, app):
    with app.app_context():
        customer = _customer("导出合成客户")
        create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id),
            120_000,
            "现金",
            "合成预收",
            "token-export-customer",
        )
        path = export_customer_workbook(db.session, customer.id, tmp_path / "customer.xlsx")

        workbook = load_workbook(path, data_only=False)
        sheet = workbook[customer.name]
        assert list(sheet.iter_rows(min_row=2, max_row=2, values_only=True))[0] == CUSTOMER_HEADERS
        assert sheet["A1"].value == customer.name
        assert "A1:M1" in {str(item) for item in sheet.merged_cells.ranges}
        assert sheet["A3"].is_date
        assert _number(sheet["J3"].value) == Decimal("1000")
        assert _number(sheet["K3"].value) == Decimal("0")
        assert _number(sheet["J4"].value) == Decimal("200")
        assert _number(sheet["K4"].value) == Decimal("-200")
        assert str(sheet["M4"].value).startswith("预收款")
        assert _number(sheet["J5"].value) == Decimal("1200")
        assert _number(sheet["K5"].value) == Decimal("-200")
        assert sheet["A5"].value == "合计"
        assert all(cell.data_type != "f" for row in sheet.iter_rows() for cell in row)


def test_summary_and_all_ledger_exports_have_contract_order(tmp_path, app):
    with app.app_context():
        customer = _customer("汇总导出客户")
        create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id),
            120_000,
            "银行转账",
            "合成已收",
            "token-export-summary",
        )
        summary_path = export_summary_workbook(
            db.session, tmp_path / "summary.xlsx", date(2026, 5, 31)
        )
        all_path = export_all_ledger_workbook(db.session, tmp_path / "all.xlsx", date(2026, 5, 31))

        summary_sheet = load_workbook(summary_path, data_only=True)["客户汇总总表"]
        assert (
            list(summary_sheet.iter_rows(min_row=1, max_row=1, values_only=True))[0]
            == SUMMARY_HEADERS
        )
        assert summary_sheet["A3"].value == "合计"
        assert _number(summary_sheet["J2"].value) == Decimal("1200")
        assert _number(summary_sheet["K2"].value) == Decimal("-200")
        assert _number(summary_sheet["J3"].value) == Decimal("1200")
        all_workbook = load_workbook(all_path, data_only=True)
        assert all_workbook.sheetnames[0] == "客户汇总总表"
        assert customer.name in all_workbook.sheetnames


def test_export_and_import_pages_are_user_facing_chinese(client, app, tmp_path):
    with app.app_context():
        customer = _customer("页面合成客户")
        app.config["EXPORTS_DIR"] = str(tmp_path / "exports")
        assert client.get(f"/customers/{customer.id}/export.xlsx").status_code == 200
    assert client.get("/exports/summary.xlsx").status_code == 200
    assert client.get("/exports/all-ledgers.xlsx").status_code == 200
    body = client.get("/imports/legacy").get_data(as_text=True)
    assert "旧账迁移" in body
    assert all(text not in body for text in ("Customer ledger", "Bookkeeping", "Read-only report"))
