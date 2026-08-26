"""Read-only legacy .xls parsing and confirmed, transactional import."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable

import xlrd
from sqlalchemy import select

from .audit_service import add_system_audit
from .backup_service import BackupError, create_backup
from .calculation_service import calculate_receivable, create_payment_allocation
from .customer_service import create_customer
from .models import Customer, ImportRecord, Payment, PaymentAllocation, Shipment
from .validation import (
    PAYMENT_METHODS,
    normalize_customer_name,
    validate_excel_safe_name,
    validate_payment_method,
)

FORMAL_COLUMN_COUNT = 13
_HEADER_WORDS = frozenset(
    {
        "日期",
        "总货款",
        "货款",
        "运费",
        "卸车费",
        "退板吨位",
        "退板金额",
        "问题扣费",
        "平方数",
        "应收款",
        "实收款",
        "欠款",
        "抹零",
        "付款方式",
        "收款方式",
    }
)
_PREPAYMENT_WORDS = ("预收", "预付", "押金", "预付款", "冲减预收")
_GENERIC_TITLES = frozenset({"客户账目", "客户明细", "客户台账", "发货明细"})
_METHOD_ALIASES = {
    "转账": "银行转账",
    "银行": "银行转账",
    "银行卡": "银行转账",
    "现款": "现金",
    "其它": "其他",
}


class LegacyImportError(ValueError):
    """A safe, user-correctable legacy import error."""


@dataclass(frozen=True)
class MappingCandidate:
    source_sheet: str
    suggested_name: str | None
    status: str
    row_count: int


@dataclass(frozen=True)
class LegacyRowCandidate:
    source_name: str
    source_hash: str
    source_sheet: str
    row_number: int
    row_kind: str
    suggested_customer_name: str | None
    shipment_date: date | None = None
    total_amount_cents: int = 0
    freight_cents: int = 0
    unloading_fee_cents: int = 0
    returned_pallet_tonnage_hundredths: int = 0
    returned_pallet_amount_cents: int = 0
    issue_deduction_cents: int = 0
    area_hundredths: int = 0
    old_receivable_cents: int | None = None
    payment_amount_cents: int = 0
    old_balance_cents: int | None = None
    rounding_cents: int = 0
    payment_method: str | None = None
    payment_description: str = ""
    prepayment_amount_cents: int = 0
    issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_key(self) -> str:
        return f"{self.source_hash}:{self.source_sheet}:{self.row_number}"

    @property
    def blocking_issues(self) -> tuple[str, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.startswith(("invalid_", "negative_", "date_"))
        )


@dataclass(frozen=True)
class LegacyPlan:
    source_name: str
    source_hash: str
    sheet_count: int
    rows: tuple[LegacyRowCandidate, ...]
    mappings: tuple[MappingCandidate, ...]
    row_counts: dict[str, int]
    anomaly_counts: dict[str, int]
    sheet_data_counts: tuple[int, ...]
    reconciliation_counts: dict[str, int] = field(default_factory=dict)
    reference_summary: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DryRunResult:
    plan: LegacyPlan
    mapping_counts: dict[str, int]
    candidate_counts: dict[str, int]
    report_path: Path | None


@dataclass(frozen=True)
class ImportResult:
    created_shipments: int
    created_payments: int
    created_allocations: int
    created_import_records: int
    skipped_existing: int
    pending_prepayments: int
    exceptions: int
    reconciliation: dict[str, int]


def _source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _row_values(sheet, row_number: int) -> tuple[object, ...]:
    values = list(sheet.row_values(row_number))
    if len(values) < FORMAL_COLUMN_COUNT:
        values.extend([None] * (FORMAL_COLUMN_COUNT - len(values)))
    return tuple(values)


def _parse_units(value: object, *, field_name: str, scale: int = 100) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        raise ValueError(f"{field_name}不是合法数字")
    text = _as_text(value).replace(",", "").replace("，", "")
    text = text.replace("￥", "").replace("¥", "").replace("元", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name}不是合法数字") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name}不是有限数字")
    number = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(number * scale)


def _parse_date(value: object, book, *, row_number: int) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        raw = value.strip().replace("/", "-").replace(".", "-")
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"第 {row_number} 行日期无法识别") from exc
    if isinstance(value, (int, float)):
        try:
            return xlrd.xldate_as_datetime(value, book.datemode).date()
        except (TypeError, ValueError, xlrd.XLDateError) as exc:
            raise ValueError(f"第 {row_number} 行日期无法识别") from exc
    raise ValueError(f"第 {row_number} 行日期无法识别")


def _date_from_cell(sheet, row_number: int, book) -> date | None:
    cell = sheet.cell(row_number, 0) if sheet.ncols else None
    if cell is None or cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(cell.value, book.datemode).date()
        except (TypeError, ValueError, xlrd.XLDateError) as exc:
            raise ValueError(f"第 {row_number + 1} 行日期无法识别") from exc
    return _parse_date(cell.value, book, row_number=row_number + 1)


def _payment_method(raw: str) -> tuple[str | None, str]:
    text = raw.strip()
    if not text:
        return None, ""
    if "未付款" in text or text == "未收款":
        return None, text
    for method in PAYMENT_METHODS:
        if method in text:
            description = text.replace(method, "", 1).strip(" -/：:；;")
            return method, description
    for alias, method in _METHOD_ALIASES.items():
        if alias in text:
            description = text.replace(alias, "", 1).strip(" -/：:；;")
            return method, description
    return None, text


def _identity(sheet_name: str, title: str) -> tuple[str | None, str]:
    sheet_clean = sheet_name.strip()
    title_clean = title.strip()
    title_is_generic = title_clean in _GENERIC_TITLES or not title_clean
    if not title_is_generic and normalize_customer_name(title_clean) != normalize_customer_name(
        sheet_clean
    ):
        if "厂" in title_clean and "零售" in title_clean:
            return "厂里零售", "needs_confirmation"
        return sheet_clean or title_clean, "conflict"
    candidate = sheet_clean or title_clean
    if any(marker in candidate for marker in ("厂内零售", "厂里零售")):
        return "厂里零售", "auto" if candidate == "厂里零售" else "needs_confirmation"
    try:
        validate_excel_safe_name(candidate)
    except ValueError:
        return None, "unrecognized"
    return candidate, "auto"


def _header_index(sheet) -> int | None:
    for row_number in range(min(sheet.nrows, 12)):
        labels = {_as_text(value) for value in _row_values(sheet, row_number) if _has_value(value)}
        if len(labels & _HEADER_WORDS) >= 5:
            return row_number
    return None


def _row_kind(values: tuple[object, ...], row_number: int, book, sheet) -> str:
    text_values = [_as_text(value) for value in values if _has_value(value)]
    extra_values = sheet.row_values(row_number)[FORMAL_COLUMN_COUNT:]
    try:
        row_date = _date_from_cell(sheet, row_number, book)
    except ValueError:
        row_date = None
    if not text_values and not any(_has_value(value) for value in extra_values):
        return "empty"
    if any(value == "合计" or value.startswith("合计") for value in text_values):
        return "total"
    if len(set(text_values) & _HEADER_WORDS) >= 5:
        return "header"
    if any(word in " ".join(text_values) for word in _PREPAYMENT_WORDS):
        shipment_values = [values[index] for index in (*range(1, 9), 11)]
        has_shipment_amount = any(_has_value(value) and value != 0 for value in shipment_values)
        if row_date is None or not has_shipment_amount:
            return "prepayment"
    if row_date is not None:
        return "business"
    if row_number < 2 and text_values:
        return "title"
    return "unrecognized"


def _candidate_from_row(
    path: Path,
    source_hash: str,
    sheet,
    sheet_name: str,
    customer_name: str | None,
    row_number: int,
    values: tuple[object, ...],
    book,
    kind: str,
) -> LegacyRowCandidate | None:
    if kind not in {"business", "prepayment"}:
        return None
    issues: list[str] = []
    shipment_date = None
    try:
        shipment_date = _date_from_cell(sheet, row_number, book)
    except ValueError:
        issues.append("date_invalid")
    if kind == "prepayment":
        try:
            amount = _parse_units(values[9], field_name="预收金额")
        except ValueError:
            amount = 0
            issues.append("invalid_prepayment_amount")
        if amount <= 0:
            issues.append("prepayment_amount_missing")
        return LegacyRowCandidate(
            source_name=path.name,
            source_hash=source_hash,
            source_sheet=sheet_name,
            row_number=row_number + 1,
            row_kind=kind,
            suggested_customer_name=customer_name,
            shipment_date=shipment_date,
            prepayment_amount_cents=amount,
            payment_method=_payment_method(_as_text(values[12]))[0],
            payment_description=_as_text(values[12]),
            issues=tuple(issues),
        )

    fields: list[int] = []
    field_names = (
        "total_amount",
        "freight",
        "unloading_fee",
        "returned_pallet_tonnage",
        "returned_pallet_amount",
        "issue_deduction",
        "area",
    )
    for index, field_name in enumerate(field_names, start=1):
        scale = 100
        try:
            parsed = _parse_units(values[index], field_name=field_name, scale=scale)
        except ValueError:
            parsed = 0
            issues.append(f"invalid_{field_name}")
        if parsed < 0:
            issues.append(f"negative_{field_name}")
        fields.append(parsed)
    try:
        rounding = _parse_units(values[11], field_name="rounding")
    except ValueError:
        rounding = 0
        issues.append("invalid_rounding")
    if rounding < 0:
        issues.append("negative_rounding")
    try:
        old_receivable = _parse_units(values[8], field_name="old_receivable")
    except ValueError:
        old_receivable = None
        issues.append("invalid_old_receivable")
    try:
        payment_amount = _parse_units(values[9], field_name="payment")
    except ValueError:
        payment_amount = 0
        issues.append("invalid_payment")
    if payment_amount < 0:
        issues.append("negative_payment")
    try:
        old_balance = _parse_units(values[10], field_name="old_balance")
    except ValueError:
        old_balance = None
        issues.append("invalid_old_balance")
    method, description = _payment_method(_as_text(values[12]))
    if payment_amount > 0 and method is None and "未付款" not in _as_text(values[12]):
        issues.append("payment_method_missing")
    if payment_amount > 0 and "未付款" in _as_text(values[12]):
        issues.append("unpaid_with_payment_amount")
    return LegacyRowCandidate(
        source_name=path.name,
        source_hash=source_hash,
        source_sheet=sheet_name,
        row_number=row_number + 1,
        row_kind=kind,
        suggested_customer_name=customer_name,
        shipment_date=shipment_date,
        total_amount_cents=fields[0],
        freight_cents=fields[1],
        unloading_fee_cents=fields[2],
        returned_pallet_tonnage_hundredths=fields[3],
        returned_pallet_amount_cents=fields[4],
        issue_deduction_cents=fields[5],
        area_hundredths=fields[6],
        old_receivable_cents=old_receivable,
        payment_amount_cents=payment_amount,
        old_balance_cents=old_balance,
        rounding_cents=rounding,
        payment_method=method,
        payment_description=description,
        issues=tuple(issues),
    )


class LegacyWorkbookParser:
    """Parse an old binary workbook without writing to the application database."""

    def parse(self, path: str | Path, existing_customer_names: Iterable[str] = ()) -> LegacyPlan:
        source = Path(path)
        if source.suffix.casefold() != ".xls":
            raise LegacyImportError("旧账迁移只接受 .xls 文件。")
        if not source.is_file():
            raise LegacyImportError("找不到旧账文件。")
        source_hash = _source_hash(source)
        try:
            book = xlrd.open_workbook(source, on_demand=True)
        except (OSError, xlrd.XLRDError) as exc:
            raise LegacyImportError("旧账文件无法读取，请确认文件未损坏。") from exc
        existing = {
            normalize_customer_name(name): name for name in existing_customer_names if name
        }
        rows: list[LegacyRowCandidate] = []
        mappings: list[MappingCandidate] = []
        row_counts: Counter[str] = Counter()
        anomaly_counts: Counter[str] = Counter()
        reconciliation_counts: Counter[str] = Counter()
        sheet_data_counts: list[int] = []
        for sheet in (book.sheet_by_index(index) for index in range(book.nsheets)):
            header_row = _header_index(sheet)
            title = _as_text(sheet.cell_value(0, 0)) if sheet.nrows else ""
            customer_name, identity_status = _identity(sheet.name, title)
            data_count = 0
            if header_row is not None:
                row_counts["header"] += 1
                for row_number in range(header_row):
                    row_counts[
                        _row_kind(_row_values(sheet, row_number), row_number, book, sheet)
                    ] += 1
                for row_number in range(header_row + 1, sheet.nrows):
                    values = _row_values(sheet, row_number)
                    kind = _row_kind(values, row_number, book, sheet)
                    row_counts[kind] += 1
                    if kind in {"business", "prepayment"}:
                        data_count += 1
                    extra_values = sheet.row_values(row_number)[FORMAL_COLUMN_COUNT:]
                    if any(_has_value(value) for value in extra_values):
                        anomaly_counts["non_formal_column_content"] += 1
                    candidate = _candidate_from_row(
                        source,
                        source_hash,
                        sheet,
                        sheet.name,
                        customer_name,
                        row_number,
                        values,
                        book,
                        kind,
                    )
                    if candidate is not None:
                        rows.append(candidate)
                        for issue in candidate.issues:
                            anomaly_counts[issue] += 1
                        if candidate.row_kind == "business":
                            preview_shipment = Shipment(
                                total_amount_cents=candidate.total_amount_cents,
                                freight_cents=candidate.freight_cents,
                                unloading_fee_cents=candidate.unloading_fee_cents,
                                returned_pallet_amount_cents=candidate.returned_pallet_amount_cents,
                                issue_deduction_cents=candidate.issue_deduction_cents,
                            )
                            computed_receivable = calculate_receivable(preview_shipment)
                            if (
                                candidate.old_receivable_cents is not None
                                and candidate.old_receivable_cents != computed_receivable
                            ):
                                reconciliation_counts["receivable_difference_rows"] += 1
                            computed_balance = (
                                computed_receivable
                                - candidate.payment_amount_cents
                                - candidate.rounding_cents
                            )
                            if (
                                candidate.old_balance_cents is not None
                                and candidate.old_balance_cents != computed_balance
                            ):
                                reconciliation_counts["balance_difference_rows"] += 1
            else:
                for row_number in range(sheet.nrows):
                    kind = _row_kind(_row_values(sheet, row_number), row_number, book, sheet)
                    row_counts[kind] += 1
            if header_row is not None:
                sheet_data_counts.append(data_count)
                status = identity_status
                if customer_name is not None and normalize_customer_name(customer_name) in existing:
                    status = "auto"
                elif status == "auto" and customer_name is None:
                    status = "unrecognized"
                mappings.append(
                    MappingCandidate(
                        source_sheet=sheet.name,
                        suggested_name=customer_name,
                        status=status,
                        row_count=data_count,
                    )
                )
        book.release_resources()
        return LegacyPlan(
            source_name=source.name,
            source_hash=source_hash,
            sheet_count=book.nsheets,
            rows=tuple(rows),
            mappings=tuple(mappings),
            row_counts=dict(row_counts),
            anomaly_counts=dict(anomaly_counts),
            sheet_data_counts=tuple(sheet_data_counts),
            reconciliation_counts=dict(reconciliation_counts),
        )


def parse_summary_reference(path: str | Path) -> dict[str, int]:
    """Collect aggregate-only information from a historical summary workbook."""

    source = Path(path)
    if source.suffix.casefold() != ".xls":
        raise LegacyImportError("历史汇总参考文件必须是 .xls。")
    try:
        book = xlrd.open_workbook(source, on_demand=True)
    except (OSError, xlrd.XLRDError) as exc:
        raise LegacyImportError("历史汇总参考文件无法读取。") from exc
    date_rows = 0
    total_rows = 0
    nonempty_rows = 0
    for sheet in (book.sheet_by_index(index) for index in range(book.nsheets)):
        for row_number in range(sheet.nrows):
            values = _row_values(sheet, row_number)
            if any(_has_value(value) for value in values):
                nonempty_rows += 1
            if any(_as_text(value).startswith("合计") for value in values if _has_value(value)):
                total_rows += 1
            try:
                if _date_from_cell(sheet, row_number, book) is not None:
                    date_rows += 1
            except ValueError:
                continue
    book.release_resources()
    return {
        "sheet_count": book.nsheets,
        "nonempty_rows": nonempty_rows,
        "date_rows": date_rows,
        "total_rows": total_rows,
    }


def _write_dry_run_report(result: DryRunResult, report_directory: str | Path) -> Path:
    directory = Path(report_directory)
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / f"dry-run-{result.plan.source_hash}.json"
    payload = {
        "source_name": result.plan.source_name,
        "source_hash": result.plan.source_hash,
        "sheet_count": result.plan.sheet_count,
        "row_counts": result.plan.row_counts,
        "mapping_counts": result.mapping_counts,
        "candidate_counts": result.candidate_counts,
        "anomaly_counts": result.plan.anomaly_counts,
        "reconciliation_counts": result.plan.reconciliation_counts,
        "reference_summary": result.plan.reference_summary,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def dry_run_legacy_import(
    session,
    source_path: str | Path,
    *,
    reference_path: str | Path | None = None,
    report_directory: str | Path = "runtime_data/import_reports",
) -> DryRunResult:
    existing_names = session.scalars(select(Customer.name)).all()
    plan = LegacyWorkbookParser().parse(source_path, existing_names)
    reference_summary = parse_summary_reference(reference_path) if reference_path else {}
    plan = LegacyPlan(
        source_name=plan.source_name,
        source_hash=plan.source_hash,
        sheet_count=plan.sheet_count,
        rows=plan.rows,
        mappings=plan.mappings,
        row_counts=plan.row_counts,
        anomaly_counts=plan.anomaly_counts,
        sheet_data_counts=plan.sheet_data_counts,
        reconciliation_counts=plan.reconciliation_counts,
        reference_summary=reference_summary,
    )
    mapping_counts = dict(Counter(mapping.status for mapping in plan.mappings))
    candidate_counts = {
        "shipment_rows": sum(row.row_kind == "business" for row in plan.rows),
        "payment_rows": sum(
            row.row_kind == "business"
            and row.payment_amount_cents > 0
            for row in plan.rows
        ),
        "payment_method_pending_rows": sum(
            row.row_kind == "business"
            and row.payment_amount_cents > 0
            and row.payment_method is None
            for row in plan.rows
        ),
        "prepayment_rows": sum(row.row_kind == "prepayment" for row in plan.rows),
        "pending_prepayment_rows": sum(
            row.row_kind == "prepayment" and row.prepayment_amount_cents > 0 for row in plan.rows
        ),
    }
    result = DryRunResult(
        plan=plan,
        mapping_counts=mapping_counts,
        candidate_counts=candidate_counts,
        report_path=None,
    )
    report_path = _write_dry_run_report(result, report_directory)
    return DryRunResult(
        plan=plan,
        mapping_counts=mapping_counts,
        candidate_counts=candidate_counts,
        report_path=report_path,
    )


def backup_sqlite_database(bind, destination: str | Path) -> Path:
    try:
        manifest = create_backup(
            bind,
            destination=destination,
            reason="before_import",
            app_version="stage-4",
        )
    except BackupError as exc:
        raise LegacyImportError(str(exc)) from exc
    return Path(destination).with_name(manifest.database_filename)


def _reconciliation(session, customer_ids: set[int]) -> dict[str, int]:
    if not customer_ids:
        return {
            "active_shipments": 0,
            "active_payments": 0,
            "active_allocations": 0,
            "receivable_cents": 0,
            "payment_cents": 0,
            "allocated_cents": 0,
            "unallocated_cents": 0,
            "shipment_balance_cents": 0,
            "net_balance_cents": 0,
            "formula_mismatches": 0,
        }
    shipments = session.scalars(
        select(Shipment).where(
            Shipment.customer_id.in_(customer_ids), Shipment.active.is_(True)
        )
    ).all()
    payments = session.scalars(
        select(Payment).where(Payment.customer_id.in_(customer_ids), Payment.active.is_(True))
    ).all()
    payment_ids = {payment.id for payment in payments}
    allocations = session.scalars(
        select(PaymentAllocation).where(
            PaymentAllocation.payment_id.in_(payment_ids or {-1}),
            PaymentAllocation.active.is_(True),
        )
    ).all()
    receivable = sum(calculate_receivable(shipment) for shipment in shipments)
    rounding = sum(shipment.rounding_cents for shipment in shipments)
    payment_total = sum(payment.amount_cents for payment in payments)
    allocated = sum(allocation.allocated_amount_cents for allocation in allocations)
    allocated_by_payment = Counter()
    for allocation in allocations:
        allocated_by_payment[allocation.payment_id] += allocation.allocated_amount_cents
    payment_by_id = {payment.id: payment.amount_cents for payment in payments}
    mismatch = int(
        allocated > payment_total
        or any(
            allocated_by_payment[payment_id] > amount
            for payment_id, amount in payment_by_id.items()
        )
    )
    return {
        "active_shipments": len(shipments),
        "active_payments": len(payments),
        "active_allocations": len(allocations),
        "receivable_cents": receivable,
        "payment_cents": payment_total,
        "allocated_cents": allocated,
        "unallocated_cents": payment_total - allocated,
        "shipment_balance_cents": receivable - allocated - rounding,
        "net_balance_cents": receivable - payment_total - rounding,
        "formula_mismatches": mismatch,
    }


def _customer_for_mapping(session, name: str) -> Customer:
    cleaned = validate_excel_safe_name(name)
    normalized = normalize_customer_name(cleaned)
    customer = session.scalar(select(Customer).where(Customer.normalized_name == normalized))
    if customer is None:
        return create_customer(session, cleaned, "旧账确认导入")
    if not customer.active:
        raise LegacyImportError("归档客户必须先恢复后才能导入。")
    return customer


def _new_import_shipment(session, customer: Customer, row: LegacyRowCandidate) -> Shipment:
    shipment = Shipment(
        customer_id=customer.id,
        shipment_date=row.shipment_date,
        total_amount_cents=row.total_amount_cents,
        freight_cents=row.freight_cents,
        unloading_fee_cents=row.unloading_fee_cents,
        returned_pallet_tonnage_hundredths=row.returned_pallet_tonnage_hundredths,
        returned_pallet_amount_cents=row.returned_pallet_amount_cents,
        issue_deduction_cents=row.issue_deduction_cents,
        area_hundredths=row.area_hundredths,
        rounding_cents=row.rounding_cents,
        description="旧账导入记录",
    )
    session.add(shipment)
    session.flush()
    return shipment


def _new_import_payment(
    session, customer: Customer, row: LegacyRowCandidate, method: str, amount: int
):
    payment = Payment(
        customer_id=customer.id,
        payment_date=row.shipment_date,
        amount_cents=amount,
        payment_method=validate_payment_method(method),
        description=row.payment_description[:200] or "旧账导入记录",
    )
    session.add(payment)
    session.flush()
    return payment


def _confirmed_payment_method(
    row: LegacyRowCandidate, payment_methods: dict[str, str]
) -> str | None:
    """Return a source method, overridden only by the exact stable source key."""

    method = payment_methods.get(row.source_key, row.payment_method)
    if isinstance(method, str):
        method = method.strip()
    return method or None


def _validated_confirmed_payment_method(
    row: LegacyRowCandidate, payment_methods: dict[str, str], *, prepayment: bool = False
) -> str:
    method = _confirmed_payment_method(row, payment_methods)
    if method is None:
        label = "预收候选" if prepayment else "实收金额所在行"
        raise LegacyImportError(f"{label}需要明确付款方式后才能导入。")
    try:
        return validate_payment_method(method)
    except ValueError as exc:
        raise LegacyImportError("付款方式只能选择：银行转账、微信、支付宝、现金或其他。") from exc


def confirm_legacy_import(
    session,
    dry_run: DryRunResult,
    backup_path: str | Path,
    *,
    confirmed_mappings: dict[str, str] | None = None,
    confirm_prepayments: bool = False,
    confirmed_payment_methods: dict[str, str] | None = None,
    backup_fn: Callable[[object, str | Path], Path] = backup_sqlite_database,
) -> ImportResult:
    """Confirm a plan, back up first, and import all selected rows atomically."""

    mappings = confirmed_mappings or {}
    payment_methods = confirmed_payment_methods or {}
    target_names: dict[str, str] = {}
    for candidate in dry_run.plan.mappings:
        target = mappings.get(candidate.source_sheet)
        if target is None:
            raise LegacyImportError("所有旧表客户映射都需要明确确认后才能导入。")
        try:
            target_names[candidate.source_sheet] = validate_excel_safe_name(target)
        except ValueError as exc:
            raise LegacyImportError(str(exc)) from exc
    for row in dry_run.plan.rows:
        if row.suggested_customer_name is not None and row.source_sheet not in target_names:
            raise LegacyImportError("旧表行缺少客户映射。")
        if row.blocking_issues:
            raise LegacyImportError("旧表存在无法识别的金额或日期，请先处理异常。")
        if row.row_kind == "business" and row.payment_amount_cents > 0:
            if "unpaid_with_payment_amount" in row.issues:
                raise LegacyImportError("旧表标记为未付款却存在实收金额，请先处理异常。")
            _validated_confirmed_payment_method(row, payment_methods)
        if row.row_kind == "prepayment" and confirm_prepayments:
            if row.prepayment_amount_cents <= 0:
                raise LegacyImportError("已确认导入的预收候选必须有大于 0 的金额。")
            if row.shipment_date is None:
                raise LegacyImportError("已确认导入的预收候选必须有日期。")
            _validated_confirmed_payment_method(row, payment_methods, prepayment=True)
    session.rollback()
    backup_fn(session.get_bind(), backup_path)
    session.rollback()
    created_shipments = 0
    created_payments = 0
    created_allocations = 0
    created_records = 0
    skipped_existing = 0
    pending_prepayments = 0
    exceptions = 0
    customer_ids: set[int] = set()
    with session.begin():
        for row in dry_run.plan.rows:
            if row.row_kind not in {"business", "prepayment"}:
                continue
            if row.source_sheet not in target_names:
                continue
            existing = session.scalar(
                select(ImportRecord).where(
                    ImportRecord.source_name == dry_run.plan.source_name,
                    ImportRecord.source_key == row.source_key,
                )
            )
            if existing is not None:
                skipped_existing += 1
                continue
            if row.row_kind == "prepayment":
                if not confirm_prepayments:
                    pending_prepayments += 1
                    continue
                method = _validated_confirmed_payment_method(row, payment_methods, prepayment=True)
                customer = _customer_for_mapping(session, target_names[row.source_sheet])
                customer_ids.add(customer.id)
                _new_import_payment(session, customer, row, method, row.prepayment_amount_cents)
                created_payments += 1
                session.add(
                    ImportRecord(
                        source_name=dry_run.plan.source_name,
                        source_key=row.source_key,
                        source_hash=dry_run.plan.source_hash,
                        status="imported",
                        message="预收候选经确认后导入",
                    )
                )
                created_records += 1
                continue
            method = None
            if row.payment_amount_cents > 0:
                method = _validated_confirmed_payment_method(row, payment_methods)
            customer = _customer_for_mapping(session, target_names[row.source_sheet])
            customer_ids.add(customer.id)
            shipment = _new_import_shipment(session, customer, row)
            created_shipments += 1
            if method is not None:
                payment = _new_import_payment(
                    session, customer, row, method, row.payment_amount_cents
                )
                created_payments += 1
                due = calculate_receivable(shipment) - shipment.rounding_cents
                allocation_amount = min(payment.amount_cents, due) if due > 0 else 0
                if allocation_amount > 0:
                    create_payment_allocation(session, payment, shipment, allocation_amount)
                    created_allocations += 1
            session.add(
                ImportRecord(
                    source_name=dry_run.plan.source_name,
                    source_key=row.source_key,
                    source_hash=dry_run.plan.source_hash,
                    status="imported",
                    message="确认导入",
                )
            )
            created_records += 1
        add_system_audit(
            session,
            "legacy_import",
            "confirmed",
            counts={
                "shipments": created_shipments,
                "payments": created_payments,
                "allocations": created_allocations,
                "records": created_records,
                "skipped": skipped_existing,
            },
        )
    return ImportResult(
        created_shipments=created_shipments,
        created_payments=created_payments,
        created_allocations=created_allocations,
        created_import_records=created_records,
        skipped_existing=skipped_existing,
        pending_prepayments=pending_prepayments,
        exceptions=exceptions,
        reconciliation=_reconciliation(session, customer_ids),
    )
