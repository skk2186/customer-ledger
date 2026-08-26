"""HTTP routes for customer, shipment, payment and read-only summary workflows."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .audit_service import add_system_audit
from .backup_service import (
    BackupError,
    create_backup,
    list_backups,
    restore_database,
    validate_backup,
)
from .bookkeeping_service import (
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
from .calculation_service import (
    customer_ledger_rows,
    customer_summary,
    payment_unallocated_cents,
    summarize_customers,
    verify_accounting_identities,
)
from .customer_service import (
    BusinessError,
    archive_customer,
    create_customer,
    restore_customer,
    update_customer,
)
from .export_service import (
    ExportError,
    export_all_ledger_workbook,
    export_customer_workbook,
    export_summary_workbook,
)
from .extensions import db
from .legacy_import_service import (
    LegacyImportError,
    confirm_legacy_import,
    dry_run_legacy_import,
)
from .models import AuditEvent, Customer, Payment, PaymentAllocation, Shipment
from .validation import (
    INITIAL_PAYMENT_OPTIONS,
    PAYMENT_METHODS,
    UNPAID_PAYMENT_OPTION,
    ValidationError,
    parse_date,
    parse_money_cents,
    parse_quantity_hundredths,
)

main_bp = Blueprint("main", __name__)


def _new_token() -> str:
    return uuid4().hex


def _cents_text(value: int) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}{value // 100}.{value % 100:02d}"


def _quantity_text(value: int) -> str:
    return f"{value // 100}.{value % 100:02d}"


def _int_form(value: str | None, field_name: str) -> int:
    try:
        result = int(value or "")
    except ValueError as exc:
        raise ValidationError(f"{field_name}格式不正确。") from exc
    if result <= 0:
        raise ValidationError(f"{field_name}格式不正确。")
    return result


def _safe_call(operation, *args):
    try:
        return operation(*args), None
    except (BusinessError, BookkeepingError, ValidationError, ValueError) as exc:
        db.session.rollback()
        return None, str(exc)
    except IntegrityError:
        db.session.rollback()
        return None, "保存失败，可能是重复提交或数据关系冲突。"
    except SQLAlchemyError:
        db.session.rollback()
        return None, "保存失败，请稍后重试。"


def _record_system_audit(action: str, *, object_type: str, counts=None) -> None:
    try:
        db.session.rollback()
        add_system_audit(
            db.session,
            object_type,
            action,
            counts=counts,
        )
        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise BackupError("操作记录保存失败，已阻止后续操作。") from exc


def _create_application_backup(reason: str):
    try:
        manifest = create_backup(
            db.engine,
            backup_dir=current_app.config["BACKUP_DIR"],
            reason=reason,
            app_version=current_app.config["APP_VERSION"],
        )
        _record_system_audit(reason, object_type="backup", counts={"files": 1})
        return manifest
    except BackupError:
        db.session.rollback()
        raise


def _page_number(value: str | None) -> int:
    try:
        return max(int(value or "1"), 1)
    except ValueError:
        return 1


def _page_info(total: int, page: int, per_page: int) -> dict[str, int | bool]:
    total_pages = max((total + per_page - 1) // per_page, 1)
    current = min(page, total_pages)
    return {
        "page": current,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": current > 1,
        "has_next": current < total_pages,
    }


def _backup_manifest_path(filename: str) -> Path:
    backup_dir = Path(current_app.config["BACKUP_DIR"]).resolve()
    candidate = (backup_dir / filename).resolve()
    if candidate.parent != backup_dir or candidate.name != filename or candidate.suffix != ".json":
        abort(404)
    if not candidate.is_file():
        abort(404)
    return candidate


def _render_backups(error: str | None = None):
    return render_template(
        "backups.html",
        backups=list_backups(current_app.config["BACKUP_DIR"]),
        error=error,
    )


def _customer_or_404(customer_id: int) -> Customer:
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)
    return customer


def _shipment_input_from_form(form) -> ShipmentInput:
    return ShipmentInput(
        customer_id=_int_form(form.get("customer_id"), "客户"),
        shipment_date=parse_date(form.get("shipment_date")),
        total_amount_cents=parse_money_cents(form.get("total_amount"), "总货款"),
        freight_cents=parse_money_cents(form.get("freight"), "运费"),
        unloading_fee_cents=parse_money_cents(form.get("unloading_fee"), "卸车费"),
        returned_pallet_tonnage_hundredths=parse_quantity_hundredths(
            form.get("returned_pallet_tonnage"), "退板吨位"
        ),
        returned_pallet_amount_cents=parse_money_cents(
            form.get("returned_pallet_amount"), "退板金额"
        ),
        issue_deduction_cents=parse_money_cents(form.get("issue_deduction"), "问题扣费"),
        area_hundredths=parse_quantity_hundredths(form.get("area"), "平方数"),
        rounding_cents=parse_money_cents(form.get("rounding"), "抹零"),
        description=form.get("description", ""),
    )


def _shipment_values(shipment: Shipment) -> dict[str, str | int]:
    return {
        "customer_id": shipment.customer_id,
        "shipment_date": shipment.shipment_date.isoformat(),
        "total_amount": _cents_text(shipment.total_amount_cents),
        "freight": _cents_text(shipment.freight_cents),
        "unloading_fee": _cents_text(shipment.unloading_fee_cents),
        "returned_pallet_tonnage": _quantity_text(shipment.returned_pallet_tonnage_hundredths),
        "returned_pallet_amount": _cents_text(shipment.returned_pallet_amount_cents),
        "issue_deduction": _cents_text(shipment.issue_deduction_cents),
        "area": _quantity_text(shipment.area_hundredths),
        "rounding": _cents_text(shipment.rounding_cents),
        "description": shipment.description,
    }


def _new_shipment_values() -> dict[str, str | int]:
    return {
        "customer_id": "",
        "shipment_date": date.today().isoformat(),
        "total_amount": "",
        "freight": "",
        "unloading_fee": "",
        "returned_pallet_tonnage": "",
        "returned_pallet_amount": "",
        "issue_deduction": "",
        "area": "",
        "rounding": "",
        "description": "",
        "initial_received": "",
        "payment_method": UNPAID_PAYMENT_OPTION,
        "payment_description": "",
    }


@main_bp.get("/")
def index():
    return render_template("index.html")


@main_bp.get("/healthz")
def healthz():
    return {"status": "ok"}


@main_bp.get("/customers")
def customers():
    query = request.args.get("q", "").strip()
    statement = select(Customer).order_by(Customer.active.desc(), Customer.name.asc())
    if query:
        statement = statement.where(
            or_(Customer.name.contains(query), Customer.normalized_name.contains(query.casefold()))
        )
    customer_list = db.session.scalars(statement).all()
    return render_template("customers.html", customers=customer_list, query=query)


@main_bp.route("/customers/new", methods=["GET", "POST"])
def new_customer():
    values = {"name": "", "notes": ""}
    error = None
    if request.method == "POST":
        values = {"name": request.form.get("name", ""), "notes": request.form.get("notes", "")}
        _, error = _safe_call(create_customer, db.session, values["name"], values["notes"])
        if error is None:
            db.session.commit()
            flash("客户已新增。", "success")
            return redirect(url_for("main.customers"))
    return render_template("customer_form.html", customer=None, values=values, error=error)


@main_bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id: int):
    customer = _customer_or_404(customer_id)
    values = {"name": customer.name, "notes": customer.notes}
    error = None
    if request.method == "POST":
        values = {"name": request.form.get("name", ""), "notes": request.form.get("notes", "")}
        _, error = _safe_call(
            update_customer, db.session, customer, values["name"], values["notes"]
        )
        if error is None:
            db.session.commit()
            flash("客户信息已保存。", "success")
            return redirect(url_for("main.customers"))
    return render_template("customer_form.html", customer=customer, values=values, error=error)


@main_bp.post("/customers/<int:customer_id>/archive")
def archive(customer_id: int):
    customer = _customer_or_404(customer_id)
    _, error = _safe_call(archive_customer, db.session, customer)
    if error is None:
        db.session.commit()
        flash("客户已归档。", "success")
    else:
        flash(error, "error")
    return redirect(url_for("main.customers"))


@main_bp.post("/customers/<int:customer_id>/restore")
def restore(customer_id: int):
    customer = _customer_or_404(customer_id)
    _, error = _safe_call(restore_customer, db.session, customer)
    if error is None:
        db.session.commit()
        flash("客户已恢复。", "success")
    else:
        flash(error, "error")
    return redirect(url_for("main.customers"))


@main_bp.route("/shipments/new", methods=["GET", "POST"])
def new_shipment():
    values = _new_shipment_values()
    token = _new_token()
    error = None
    if request.method == "POST":
        values.update(request.form.to_dict())
        token = request.form.get("submission_token", token)
        result, error = _safe_call(
            _create_shipment_from_form,
            request.form,
            token,
        )
        if error is None:
            flash("发货已保存。", "success")
            return redirect(url_for("main.customer_ledger", customer_id=result.customer_id))
    active_customers = db.session.scalars(
        select(Customer).where(Customer.active.is_(True)).order_by(Customer.name.asc())
    ).all()
    return render_template(
        "shipment_form.html",
        customer=None,
        customers=active_customers,
        values=values,
        error=error,
        token=token,
        payment_methods=INITIAL_PAYMENT_OPTIONS,
    )


def _create_shipment_from_form(form, token: str):
    data = _shipment_input_from_form(form)
    initial = parse_money_cents(form.get("initial_received"), "初始实收款")
    payment_method = form.get("payment_method", "")
    if payment_method == UNPAID_PAYMENT_OPTION:
        if initial != 0:
            raise ValidationError("选择“暂未付款”时，初始收款金额必须为空或 0。")
        # The unpaid shortcut is a UI choice, never a persisted Payment method.
        payment_method = PAYMENT_METHODS[0]
    return create_shipment_with_initial_payment(
        db.session,
        data,
        initial,
        payment_method,
        form.get("payment_description", ""),
        token,
    )


@main_bp.route("/shipments/<int:shipment_id>/edit", methods=["GET", "POST"])
def edit_shipment(shipment_id: int):
    shipment = db.session.get(Shipment, shipment_id)
    if shipment is None:
        abort(404)
    customer = _customer_or_404(shipment.customer_id)
    values = _shipment_values(shipment)
    token = _new_token()
    error = None
    if request.method == "POST":
        values.update(request.form.to_dict())
        token = request.form.get("submission_token", token)
        result, error = _safe_call(
            _update_shipment_from_form,
            shipment.id,
            request.form,
            token,
        )
        if error is None:
            flash("发货已修改。", "success")
            return redirect(url_for("main.customer_ledger", customer_id=result.customer_id))
    return render_template(
        "shipment_form.html",
        customer=customer,
        customers=[],
        values=values,
        error=error,
        token=token,
        payment_methods=PAYMENT_METHODS,
    )


def _update_shipment_from_form(shipment_id: int, form, token: str):
    return update_shipment(
        db.session, shipment_id, _shipment_input_from_form(form), token
    )


@main_bp.post("/shipments/<int:shipment_id>/void")
def void_shipment_route(shipment_id: int):
    shipment = db.session.get(Shipment, shipment_id)
    if shipment is None:
        abort(404)
    customer_id = shipment.customer_id
    _, error = _safe_call(
        void_shipment,
        db.session,
        shipment_id,
        request.form.get("submission_token", ""),
    )
    if error is None:
        flash("发货已作废，相关分配已撤销并转为预收。", "success")
    else:
        flash(error, "error")
    return redirect(url_for("main.customer_ledger", customer_id=customer_id))


@main_bp.get("/customers/<int:customer_id>/ledger")
def customer_ledger(customer_id: int):
    customer = _customer_or_404(customer_id)
    per_page = int(current_app.config["LEDGER_PAGE_SIZE"])
    requested_page = _page_number(request.args.get("page"))
    shipment_total = db.session.scalar(
        select(func.count(Shipment.id)).where(Shipment.customer_id == customer.id)
    )
    shipment_page = _page_info(int(shipment_total or 0), requested_page, per_page)
    rows = customer_ledger_rows(
        db.session,
        customer.id,
        limit=per_page,
        offset=(int(shipment_page["page"]) - 1) * per_page,
    )
    summary = customer_summary(db.session, customer.id)
    payment_total = db.session.scalar(
        select(func.count(Payment.id)).where(Payment.customer_id == customer.id)
    )
    payment_page = _page_info(int(payment_total or 0), requested_page, per_page)
    payments = db.session.scalars(
        select(Payment)
        .where(Payment.customer_id == customer.id)
        .order_by(Payment.payment_date.desc(), Payment.id.desc())
        .limit(per_page)
        .offset((int(payment_page["page"]) - 1) * per_page)
    ).all()
    active_shipments = [row.shipment for row in rows if row.shipment.active]
    payment_rows = [
        {
            "payment": payment,
            "unallocated_cents": payment_unallocated_cents(db.session, payment),
            "allocations": payment.allocations,
        }
        for payment in payments
    ]
    return render_template(
        "customer_ledger.html",
        customer=customer,
        rows=rows,
        summary=summary,
        payment_rows=payment_rows,
        active_shipments=active_shipments,
        shipment_page=shipment_page,
        payment_page=payment_page,
        token=_new_token(),
    )


def _export_path(filename: str):
    directory = current_app.config["EXPORTS_DIR"]
    return Path(directory) / filename


@main_bp.get("/customers/<int:customer_id>/export.xlsx")
def export_customer(customer_id: int):
    customer = _customer_or_404(customer_id)
    try:
        _create_application_backup("before_export")
        path = export_customer_workbook(
            db.session, customer.id, _export_path(f"customer-{customer.id}.xlsx")
        )
    except BackupError as exc:
        return f"导出未完成：{exc}", 503
    except (ExportError, OSError):
        db.session.rollback()
        return "导出未完成，请检查本机导出目录后重试。", 400
    return send_file(path, as_attachment=True, download_name=f"{customer.name}.xlsx")


@main_bp.get("/exports/summary.xlsx")
def export_summary():
    cutoff_raw = request.args.get("as_of", date.today().isoformat())
    try:
        cutoff = parse_date(cutoff_raw, "截至日期")
    except ValidationError as exc:
        return str(exc), 400
    try:
        _create_application_backup("before_export")
        path = export_summary_workbook(db.session, _export_path("customer-summary.xlsx"), cutoff)
    except BackupError as exc:
        return f"导出未完成：{exc}", 503
    except (ExportError, OSError):
        db.session.rollback()
        return "导出未完成，请检查本机导出目录后重试。", 400
    return send_file(path, as_attachment=True, download_name="客户汇总总表.xlsx")


@main_bp.get("/exports/all-ledgers.xlsx")
def export_all_ledgers():
    cutoff_raw = request.args.get("as_of", date.today().isoformat())
    try:
        cutoff = parse_date(cutoff_raw, "截至日期")
    except ValidationError as exc:
        return str(exc), 400
    try:
        _create_application_backup("before_export")
        path = export_all_ledger_workbook(
            db.session, _export_path("all-customer-ledgers.xlsx"), cutoff
        )
    except BackupError as exc:
        return f"导出未完成：{exc}", 503
    except (ExportError, OSError):
        db.session.rollback()
        return "导出未完成，请检查本机导出目录后重试。", 400
    return send_file(path, as_attachment=True, download_name="客户账目总表.xlsx")


@main_bp.get("/backups")
def backups():
    return _render_backups()


@main_bp.post("/backups/create")
def create_manual_backup():
    try:
        _create_application_backup("manual")
    except BackupError as exc:
        return _render_backups(f"备份未完成：{exc}"), 503
    flash("本机账库备份已保存。", "success")
    return redirect(url_for("main.backups"))


@main_bp.route("/backups/<path:manifest_filename>/restore", methods=["GET", "POST"])
def restore_backup(manifest_filename: str):
    manifest_path = _backup_manifest_path(manifest_filename)
    try:
        selected = validate_backup(
            manifest_path,
            expected_schema_version=_current_schema_version(),
        )
    except BackupError as exc:
        return _render_backups(f"备份不可恢复：{exc}"), 400
    if request.method == "GET":
        return render_template("restore_confirm.html", backup=selected)
    if request.form.get("confirm_restore") != "yes":
        return render_template(
            "restore_confirm.html",
            backup=selected,
            error="请勾选二次确认后再恢复。",
        ), 400
    try:
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()
        restore_database(
            db.engine,
            manifest_path,
            backup_dir=current_app.config["BACKUP_DIR"],
            app_version=current_app.config["APP_VERSION"],
            post_restore_check=lambda: verify_accounting_identities(db.session),
        )
        db.session.remove()
        _record_system_audit(
            "completed",
            object_type="restore",
            counts={"backups": 1},
        )
    except (BackupError, OSError, ValueError) as exc:
        db.session.rollback()
        return _render_backups(f"恢复未完成：{exc}"), 503
    flash("账库已恢复，请重新核对客户账目和汇总。", "success")
    return redirect(url_for("main.backups"))


def _current_schema_version() -> str:
    """Read the current Alembic revision without exposing it in the UI."""

    from .backup_service import current_schema_version

    database = Path(db.engine.url.database).resolve()
    return current_schema_version(database)


_AUDIT_OBJECT_LABELS = {
    "customer": "客户",
    "shipment": "发货",
    "payment": "收款",
    "payment_allocation": "收款分配",
    "legacy_import": "旧账迁移",
    "backup": "备份",
    "restore": "恢复",
}
_AUDIT_ACTION_LABELS = {
    "created": "新增",
    "updated": "修改",
    "voided": "作废",
    "archived": "归档",
    "restored": "恢复",
    "revoked": "撤销",
    "confirmed": "确认完成",
    "daily_startup": "每日启动备份",
    "before_export": "导出前备份",
    "before_import": "导入前备份",
    "before_restore": "恢复前备份",
    "manual": "手动备份",
    "completed": "完成恢复",
}


@main_bp.get("/audit")
def audit():
    events = db.session.scalars(
        select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(200)
    ).all()
    rows = [
        {
            "created_at": event.created_at,
            "object_label": _AUDIT_OBJECT_LABELS.get(event.object_type, "系统操作"),
            "action_label": _AUDIT_ACTION_LABELS.get(event.action, "已记录操作"),
        }
        for event in events
    ]
    return render_template("audit.html", audit_rows=rows)


@main_bp.route("/imports/legacy", methods=["GET", "POST"])
def legacy_import():
    dry_run = None
    import_result = None
    error = None
    payment_confirmation_rows = []
    source_path = request.form.get("source_path", "").strip()
    if request.method == "POST":
        try:
            dry_run = dry_run_legacy_import(
                db.session,
                source_path,
                reference_path=request.form.get("reference_path", "").strip() or None,
                report_directory=current_app.config["IMPORT_REPORT_DIR"],
            )
            payment_confirmation_rows = [
                row
                for row in dry_run.plan.rows
                if (
                    row.row_kind == "business"
                    and row.payment_amount_cents > 0
                    and row.payment_method is None
                )
                or (
                    row.row_kind == "prepayment"
                    and (
                        row.prepayment_amount_cents <= 0
                        or row.shipment_date is None
                        or row.payment_method is None
                    )
                )
            ]
            if request.form.get("action") == "confirm":
                confirmed_mappings = {
                    f"{mapping.source_sheet}": request.form.get(f"mapping_{index}", "").strip()
                    for index, mapping in enumerate(dry_run.plan.mappings)
                }
                confirmed_payment_methods = {
                    row.source_key: request.form.get(
                        f"payment_method_{row.source_key}", ""
                    ).strip()
                    for row in payment_confirmation_rows
                }
                backup_path = str(
                    Path(current_app.config["BACKUP_DIR"])
                    / f"ledger-before-import-{dry_run.plan.source_hash[:16]}.db"
                )
                import_result = confirm_legacy_import(
                    db.session,
                    dry_run,
                    backup_path,
                    confirmed_mappings=confirmed_mappings,
                    confirm_prepayments=request.form.get("confirm_prepayments") == "yes",
                    confirmed_payment_methods=confirmed_payment_methods,
                )
                flash("旧账已确认导入，结果已完成对账。", "success")
        except LegacyImportError as exc:
            db.session.rollback()
            error = str(exc)
        except (OSError, ValueError):
            db.session.rollback()
            error = "旧账操作未完成，请检查本机文件和备份目录后重试。"
    return render_template(
        "legacy_import.html",
        dry_run=dry_run,
        import_result=import_result,
        error=error,
        source_path=source_path,
        payment_confirmation_rows=payment_confirmation_rows,
        payment_methods=PAYMENT_METHODS,
    )


@main_bp.route("/payments/new", methods=["GET", "POST"])
def new_payment():
    selected_customer_id = request.args.get("customer_id", "")
    values = {
        "customer_id": selected_customer_id,
        "payment_date": date.today().isoformat(),
        "amount": "",
        "payment_method": PAYMENT_METHODS[0],
        "description": "",
        "allocation_mode": "none",
        "shipment_id": "",
        "allocation_amount": "",
    }
    token = _new_token()
    error = None
    if request.method == "POST":
        values.update(request.form.to_dict())
        token = request.form.get("submission_token", token)
        result, error = _safe_call(_create_payment_from_form, request.form, token)
        if error is None:
            flash("收款已保存。", "success")
            return redirect(url_for("main.customer_ledger", customer_id=result.customer_id))
        selected_customer_id = request.form.get("customer_id", "")
    active_customers = db.session.scalars(
        select(Customer).where(Customer.active.is_(True)).order_by(Customer.name.asc())
    ).all()
    selected_shipments = []
    if str(selected_customer_id).isdigit():
        selected_shipments = db.session.scalars(
            select(Shipment)
            .where(
                Shipment.customer_id == int(selected_customer_id),
                Shipment.active.is_(True),
            )
            .order_by(Shipment.shipment_date.asc(), Shipment.id.asc())
        ).all()
    return render_template(
        "payment_form.html",
        customers=active_customers,
        shipments=selected_shipments,
        values=values,
        error=error,
        token=token,
        payment_methods=PAYMENT_METHODS,
    )


def _create_payment_from_form(form, token: str):
    customer_id = _int_form(form.get("customer_id"), "客户")
    mode = form.get("allocation_mode", "none")
    allocations = []
    if mode == "specified":
        allocations.append(
            AllocationInput(
                shipment_id=_int_form(form.get("shipment_id"), "发货"),
                amount_cents=parse_money_cents(
                    form.get("allocation_amount"), "分配金额", required=True
                ),
            )
        )
    return create_payment_workflow(
        db.session,
        PaymentInput(
            customer_id=customer_id,
            payment_date=parse_date(form.get("payment_date")),
            amount_cents=parse_money_cents(form.get("amount"), "收款金额", required=True),
            payment_method=form.get("payment_method", ""),
            description=form.get("description", ""),
        ),
        mode,
        allocations,
        token,
    )


@main_bp.post("/payments/<int:payment_id>/allocate")
def allocate_payment_route(payment_id: int):
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        abort(404)
    customer_id = payment.customer_id
    try:
        allocation = AllocationInput(
            shipment_id=_int_form(request.form.get("shipment_id"), "发货"),
            amount_cents=parse_money_cents(
                request.form.get("allocation_amount"), "分配金额", required=True
            ),
        )
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.customer_ledger", customer_id=customer_id))
    _, error = _safe_call(
        allocate_existing_payment,
        db.session,
        payment_id,
        [allocation],
        request.form.get("submission_token", ""),
    )
    flash(error or "收款分配已保存。", "error" if error else "success")
    return redirect(url_for("main.customer_ledger", customer_id=customer_id))


@main_bp.post("/payments/<int:payment_id>/void")
def void_payment_route(payment_id: int):
    payment = db.session.get(Payment, payment_id)
    if payment is None:
        abort(404)
    customer_id = payment.customer_id
    _, error = _safe_call(
        void_payment,
        db.session,
        payment_id,
        request.form.get("submission_token", ""),
    )
    flash(error or "收款已作废，相关分配已撤销。", "error" if error else "success")
    return redirect(url_for("main.customer_ledger", customer_id=customer_id))


@main_bp.post("/allocations/<int:allocation_id>/revoke")
def revoke_allocation_route(allocation_id: int):
    allocation_record = db.session.get(PaymentAllocation, allocation_id)
    if allocation_record is None:
        abort(404)
    payment = db.session.get(Payment, allocation_record.payment_id)
    if payment is None:
        abort(404)
    customer_id = payment.customer_id
    _, error = _safe_call(
        revoke_allocation,
        db.session,
        allocation_id,
        request.form.get("submission_token", ""),
    )
    flash(error or "分配已撤销，金额已回到预收。", "error" if error else "success")
    return redirect(url_for("main.customer_ledger", customer_id=customer_id))


@main_bp.route("/retail/new", methods=["GET", "POST"])
def new_retail():
    values = {
        "retail_date": date.today().isoformat(),
        "location_description": "",
        "area": "",
        "amount": "",
        "received": "no",
        "payment_method": PAYMENT_METHODS[0],
        "payment_description": "",
    }
    token = _new_token()
    error = None
    if request.method == "POST":
        values.update(request.form.to_dict())
        token = request.form.get("submission_token", token)
        result, error = _safe_call(_create_retail_from_form, request.form, token)
        if error is None:
            flash("厂里零售已保存。", "success")
            return redirect(url_for("main.customer_ledger", customer_id=result.customer_id))
    return render_template(
        "retail_form.html",
        values=values,
        error=error,
        token=token,
        payment_methods=PAYMENT_METHODS,
    )


def _create_retail_from_form(form, token: str):
    return create_retail_workflow(
        db.session,
        RetailInput(
            retail_date=parse_date(form.get("retail_date")),
            location_description=form.get("location_description", ""),
            area_hundredths=parse_quantity_hundredths(form.get("area"), "平方数"),
            amount_cents=parse_money_cents(form.get("amount"), "金额"),
            received=form.get("received") == "yes",
            payment_method=form.get("payment_method", ""),
            payment_description=form.get("payment_description", ""),
        ),
        token,
    )


@main_bp.get("/summary")
def summary():
    cutoff_raw = request.args.get("as_of", date.today().isoformat())
    error = None
    try:
        cutoff = parse_date(cutoff_raw, "截至日期")
    except ValidationError as exc:
        cutoff = date.today()
        error = str(exc)
    summary_rows, grand_total = summarize_customers(db.session, cutoff)
    return render_template(
        "summary.html",
        cutoff=cutoff,
        summary_rows=summary_rows,
        grand_total=grand_total,
        error=error,
    ), 400 if error else 200
