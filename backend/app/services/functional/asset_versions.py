"""Immutable version history for requirements (cases live in testcase_lifecycle)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.functional.requirement import Requirement, RequirementVersion


def requirement_snapshot(req: Requirement) -> dict[str, Any]:
    return {
        "title": req.title,
        "body_md": req.body_md or "",
        "status": req.status,
        "priority": req.priority,
        "acceptance_rules": req.acceptance_rules or "",
        "attachments": [
            {"id": a.id, "filename": a.filename, "mime": a.mime, "size_bytes": a.size_bytes}
            for a in sorted(req.attachments or [], key=lambda a: a.id)
        ],
    }


def _add_version(
    db: Session,
    req: Requirement,
    *,
    version_no: int,
    source: str,
    note: str | None,
    actor_id: int | None,
) -> RequirementVersion:
    version = RequirementVersion(
        requirement_id=req.id,
        project_id=req.project_id,
        version_no=version_no,
        snapshot=requirement_snapshot(req),
        change_source=(source or "edit")[:32],
        change_note=(note or "").strip()[:512] or None,
        created_by_id=actor_id,
    )
    db.add(version)
    db.flush()
    return version


def ensure_baseline_requirement_version(
    db: Session,
    req: Requirement,
    *,
    source: str = "migration",
    actor_id: int | None = None,
) -> RequirementVersion:
    existing = (
        db.query(RequirementVersion)
        .filter(
            RequirementVersion.requirement_id == req.id,
            RequirementVersion.version_no == (req.version_no or 1),
        )
        .first()
    )
    if existing is not None:
        return existing
    req.version_no = req.version_no or 1
    return _add_version(
        db,
        req,
        version_no=req.version_no,
        source=source,
        note="迁移基准版本" if source == "migration" else None,
        actor_id=actor_id,
    )


def snapshot_requirement_version(
    db: Session,
    req: Requirement,
    *,
    source: str = "edit",
    note: str | None = None,
    actor_id: int | None = None,
) -> RequirementVersion:
    req.version_no = max(1, int(req.version_no or 1)) + 1
    return _add_version(
        db,
        req,
        version_no=req.version_no,
        source=source,
        note=note,
        actor_id=actor_id,
    )
