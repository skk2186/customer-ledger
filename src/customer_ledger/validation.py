"""Input validation and normalization shared by routes and services."""

from __future__ import annotations

import unicodedata


class ValidationError(ValueError):
    """A user-correctable validation error."""


EXCEL_SHEET_FORBIDDEN_CHARS = frozenset(':\\/?*[]')
EXCEL_SHEET_NAME_MAX_LENGTH = 31


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
