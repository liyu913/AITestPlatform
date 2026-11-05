"""Shared filters for functional test case list / export."""

from __future__ import annotations

from sqlalchemy.orm import Query, Session

from app.models.functional.testcase import TestCase

EXPORT_CASE_LIMIT = 5000

# Filters accepted from older clients that used the merged status column.
_LIFECYCLE_ALIASES = {
    "ready": "confirmed",
    "active": "confirmed",
    "done": "confirmed",
    "new": "draft",
    "todo": "draft",
}


def normalize_lifecycle_filter(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return _LIFECYCLE_ALIASES.get(value, value)


def apply_case_filters(
    q: Query,
    *,
    project_id: int,
    search: str | None = None,
    requirement_id: int | None = None,
    module: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    lifecycle_status: str | None = None,
    last_result: str | None = None,
    include_archived: bool = False,
) -> Query:
    filtered = q.filter(TestCase.project_id == project_id)
    if requirement_id is not None:
        filtered = filtered.filter(TestCase.requirement_id == requirement_id)
    if search and search.strip():
        pat = f"%{search.strip()}%"
        filtered = filtered.filter(TestCase.title.like(pat))
    mod = (module or "").strip()
    if mod:
        filtered = filtered.filter(TestCase.module == mod)
    pri = (priority or "").strip()
    if pri:
        filtered = filtered.filter(TestCase.priority == pri)

    # ``status`` is the deprecated argument; it now means "lifecycle".
    wanted = normalize_lifecycle_filter(lifecycle_status or status)
    if wanted:
        filtered = filtered.filter(TestCase.lifecycle_status == wanted)
        if wanted == "archived":
            # Asking for the archive explicitly must not be filtered out again.
            include_archived = True
    if last_result and last_result.strip():
        filtered = filtered.filter(TestCase.last_result == last_result.strip().lower())
    if not include_archived:
        filtered = filtered.filter(TestCase.lifecycle_status != "archived")
    return filtered


def distinct_modules(db: Session, project_id: int) -> list[str]:
    rows = (
        db.query(TestCase.module)
        .filter(
            TestCase.project_id == project_id,
            TestCase.module.isnot(None),
            TestCase.module != "",
        )
        .distinct()
        .order_by(TestCase.module.asc())
        .all()
    )
    out: list[str] = []
    for (m,) in rows:
        cleaned = (m or "").strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out
