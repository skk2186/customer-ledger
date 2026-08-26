"""Input validation and normalization shared by routes and services."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation


class ValidationError(ValueError):
    """A user-correctable validation error."""


EXCEL_SHEET_FORBIDDEN_CHARS = frozenset(':\\/?*[]')
EXCEL_SHEET_NAME_MAX_LENGTH = 31
PAYMENT_METHODS = ("银行转账", "微信", "支付宝", "现金", "其他")
UNPAID_PAYMENT_OPTION = "暂未付款"
INITIAL_PAYMENT_OPTIONS = (*PAYMENT_METHODS[:4], UNPAID_PAYMENT_OPTION, PAYMENT_METHODS[4])
DECIMAL_INPUT_PATTERN = re.compile(r"^-?[0-9]+(?:\.[0-9]{1,2})?$")
SUBMISSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def normalize_customer_name(name: str) -> str:
    """Return the canonical comparison form used for uniqueness."""

    return " ".join(unicodedata.normalize("NFKC", name).strip().split()).casefold()


def validate_excel_safe_name(name: str) -> str:
    """Validate the one shared contract for customer names and Excel Sheet names."""

    if not isinstance(name, str):
        raise ValidationError("名称必须是文本。")
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("名称不能为空。")
    if len(cleaned) > EXCEL_SHEET_NAME_MAX_LENGTH:
        raise ValidationError("名称将直接用作 Excel Sheet 名，不能超过 31 个字符。")
    if any(char in EXCEL_SHEET_FORBIDDEN_CHARS for char in cleaned):
        raise ValidationError("名称不能包含 Excel 禁止字符：: \\ / ? * [ ]。")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise ValidationError("名称不能包含控制字符。")
    if cleaned.startswith("'") or cleaned.endswith("'"):
        raise ValidationError("名称不能以单引号开头或结尾。")
    return cleaned


validate_customer_name = validate_excel_safe_name
validate_excel_sheet_name = validate_excel_safe_name


def parse_decimal_units(value: str | None, field_name: str, *, required: bool = False) -> int:
    """Parse a non-negative decimal string into hundredths without passing through float."""

    raw = "" if value is None else value.strip() if isinstance(value, str) else value
    if raw == "":
        if required:
            raise ValidationError(f"{field_name}不能为空。")
        return 0
    if not isinstance(raw, str) or not DECIMAL_INPUT_PATTERN.fullmatch(raw):
        raise ValidationError(f"{field_name}必须是非负数字，最多两位小数，不能使用科学计数法。")
    if raw.startswith("-"):
        raise ValidationError(f"{field_name}不能为负数。")
    try:
        decimal_value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError(f"{field_name}不是合法数字。") from exc
    if not decimal_value.is_finite():
        raise ValidationError(f"{field_name}不能是 NaN 或 Infinity。")
    return int(decimal_value * 100)


def parse_money_cents(value: str | None, field_name: str, *, required: bool = False) -> int:
    return parse_decimal_units(value, field_name, required=required)


def parse_quantity_hundredths(
    value: str | None, field_name: str, *, required: bool = False
) -> int:
    return parse_decimal_units(value, field_name, required=required)


def ensure_integer_units(value: int, field_name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field_name}必须使用整数单位。")
    if value < 0 or (positive and value == 0):
        qualifier = "大于 0" if positive else "非负"
        raise ValidationError(f"{field_name}必须是{qualifier}整数单位。")
    return value


def parse_date(value: str | date | None, field_name: str = "日期") -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name}不能为空。")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError(f"{field_name}格式不正确。") from exc


def validate_payment_method(value: str) -> str:
    if value not in PAYMENT_METHODS:
        raise ValidationError("付款方式只能选择：银行转账、微信、支付宝、现金或其他。")
    return value


def validate_submission_token(value: str | None) -> str:
    if not isinstance(value, str) or not SUBMISSION_TOKEN_PATTERN.fullmatch(value):
        raise ValidationError("提交令牌无效，请刷新页面后重试。")
    return value
