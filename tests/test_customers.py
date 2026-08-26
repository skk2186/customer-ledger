import pytest

from customer_ledger.customer_service import (
    BusinessError,
    archive_customer,
    create_customer,
    delete_customer,
    restore_customer,
    update_customer,
)
from customer_ledger.extensions import db
from customer_ledger.models import AuditEvent, Customer, Shipment
from customer_ledger.validation import (
    ValidationError,
    validate_customer_name,
    validate_excel_sheet_name,
)


def test_normalized_duplicate_including_archived_and_lifecycle(app):
    with app.app_context():
        customer = create_customer(db.session, "  Acme  ", "内部备注")
        db.session.commit()
        archive_customer(db.session, customer)
        db.session.commit()
        assert customer.active is False

        with pytest.raises(BusinessError, match="已存在"):
            create_customer(db.session, "ＡＣＭＥ")

        restore_customer(db.session, customer)
        db.session.commit()
        assert customer.active is True
        latest_audit = db.session.scalar(
            db.select(AuditEvent)
            .where(AuditEvent.object_id == str(customer.id))
            .order_by(AuditEvent.id.desc())
        )
        assert latest_audit.action == "restored"


def test_customer_name_rejects_excel_forbidden_character(app):
    with app.app_context(), pytest.raises(ValidationError, match="Excel"):
        create_customer(db.session, "客户/分组")


def test_sheet_name_contract():
    assert validate_customer_name is validate_excel_sheet_name
    assert validate_excel_sheet_name("客户表") == "客户表"
    with pytest.raises(ValidationError, match="禁止字符"):
        validate_excel_sheet_name("客户:表")
    with pytest.raises(ValidationError, match="31"):
        validate_excel_sheet_name("a" * 32)
    with pytest.raises(ValidationError, match="单引号"):
        validate_customer_name("'客户")
    with pytest.raises(ValidationError, match="单引号"):
        validate_customer_name("客户'")
    with pytest.raises(ValidationError, match="控制字符"):
        validate_customer_name("客户名")


def test_31_character_name_can_be_created_and_updated(app):
    with app.app_context():
        name_31 = "客" * 31
        updated_name_31 = "户" * 31
        customer = create_customer(db.session, name_31)
        db.session.commit()
        update_customer(db.session, customer, updated_name_31)
        db.session.commit()
        assert customer.name == updated_name_31


def test_32_character_name_is_rejected_by_service_and_http(client, app):
    with app.app_context(), pytest.raises(ValidationError, match="31"):
        create_customer(db.session, "客" * 32)

    response = client.get("/customers/new")
    assert response.status_code == 200
    assert 'maxlength="31"' in response.get_data(as_text=True)
    assert "直接用作 Excel Sheet 名" in response.get_data(as_text=True)

    response = client.post("/customers/new", data={"name": "客" * 32, "notes": ""})
    assert response.status_code == 200
    assert "31" in response.get_data(as_text=True)

    with app.app_context():
        customer = create_customer(db.session, "可编辑客户")
        db.session.commit()
        customer_id = customer.id
    response = client.post(
        f"/customers/{customer_id}/edit",
        data={"name": "改" * 32, "notes": ""},
    )
    assert response.status_code == 200
    assert "31" in response.get_data(as_text=True)


def test_customer_http_crud_archive_restore(client, app):
    response = client.post("/customers/new", data={"name": "网页客户", "notes": "备注"})
    assert response.status_code == 302
    with app.app_context():
        customer = db.session.scalar(db.select(Customer).where(Customer.name == "网页客户"))
        customer_id = customer.id

    response = client.post(f"/customers/{customer_id}/archive", follow_redirects=True)
    assert response.status_code == 200
    assert "已归档" in response.get_data(as_text=True)
    response = client.post(f"/customers/{customer_id}/restore", follow_redirects=True)
    assert response.status_code == 200
    assert "已恢复" in response.get_data(as_text=True)


def test_customer_with_accounting_history_cannot_be_physically_deleted(app):
    with app.app_context():
        customer = create_customer(db.session, "有历史客户")
        db.session.flush()
        db.session.add(Shipment(customer_id=customer.id, total_amount_cents=1))
        db.session.commit()

        with pytest.raises(BusinessError, match="不能物理删除"):
            delete_customer(db.session, customer)
