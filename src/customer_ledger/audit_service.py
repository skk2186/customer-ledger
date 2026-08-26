"""Small helpers for safe, non-sensitive system audit entries."""

from __future__ import annotations

import json

from .models import AuditEvent


def add_system_audit(
    session,
    object_type: str,
    action: str,
    *,
    object_id: str = "system",
    counts: dict[str, int] | None = None,
) -> AuditEvent:
    """Add an audit event containing only operation metadata and anonymous counts."""

    summary = json.dumps(counts or {}, ensure_ascii=False, separators=(",", ":"))
    event = AuditEvent(
        object_type=object_type,
        object_id=object_id,
        action=action,
        before_summary="",
        after_summary=summary,
    )
    session.add(event)
    return event
