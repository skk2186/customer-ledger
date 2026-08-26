"""Input validation and normalization shared by routes and services."""

from __future__ import annotations

import unicodedata


class ValidationError(ValueError):
    """A user-correctable validation error."""


EXCEL_SHEET_FORBIDDEN_CHARS = frozenset(':\\/?*[]')


def normalize_customer_name(name: str) -> str:
    """Return the canonical comparison form used for uniqueness."""

    return " ".join(unicodedata.normalize("NFKC", name).strip().split()).casefold()


def validate_customer_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValidationError("客户名称必须是文本。")
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("客户名称不能为空。")
    if len(cleaned) > 100:
        raise ValidationError("客户名称不能超过 100 个字符。")
    if any(char in EXCEL_SHEET_FORBIDDEN_CHARS for char in cleaned):
        raise ValidationError("客户名称不能包含 Excel 禁止字符：: \\ / ? * [ ]。")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise ValidationError("客户名称不能包含控制字符。")
    return cleaned


def validate_excel_sheet_name(name: str) -> str:
    """Validate the future Excel adapter's Sheet-name contract."""

    if not isinstance(name, str):
        raise ValidationError("Sheet 名称必须是文本。")
    cleaned = name.strip()
    if not cleaned:
        raise ValidationError("Sheet 名称不能为空。")
    if len(cleaned) > 31:
        raise ValidationError("Sheet 名称不能超过 31 个字符。")
    if any(char in EXCEL_SHEET_FORBIDDEN_CHARS for char in cleaned):
        raise ValidationError("Sheet 名称包含 Excel 禁止字符：: \\ / ? * [ ]。")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise ValidationError("Sheet 名称不能包含控制字符。")
    if cleaned.startswith("'") or cleaned.endswith("'"):
        raise ValidationError("Sheet 名称不能以单引号开头或结尾。")
    return cleaned
