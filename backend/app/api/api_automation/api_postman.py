from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.api_automation.api_automation import ApiCase
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.api_automation.api_assets import PostmanExportBody, PostmanImportBody
from app.services.api_automation.postman_io import export_postman_collection, import_postman_collection

router = APIRouter(prefix="/projects/{project_id}/api-postman", tags=["api-postman"])


@router.post("/import")
def import_postman(
    project_id: int,
    body: PostmanImportBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        payloads = import_postman_collection(body.content)
    except Exception as ex:  # noqa: BLE001
        raise AppError("bad_request", f"Postman 导入失败: {ex}", status_code=400) from ex
    created_ids: list[int] = []
    if body.create_cases:
        for p in payloads:
            case = ApiCase(
                project_id=project_id,
                name=p["name"][:512],
                method=p["method"],
                path=p["path"],
                query_json=p.get("query") or {},
                headers_json=p.get("headers") or {},
                body_type=p.get("body_type") or "none",
                body_text=p.get("body") or "",
                assertions_json=p.get("assertions") or [],
                extractors_json=p.get("extractors") or [],
                timeout_ms=p.get("timeout_ms") or 15000,
                status=p.get("status") or "draft",
                creator_id=user.id,
            )
            db.add(case)
            db.flush()
            created_ids.append(case.id)
        db.commit()
    return {"count": len(payloads), "created_ids": created_ids, "preview": payloads[:20]}


@router.post("/export")
def export_postman(
    project_id: int,
    body: PostmanExportBody,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> dict:
    q = db.query(ApiCase).filter(ApiCase.project_id == project_id)
    if body.case_ids:
        q = q.filter(ApiCase.id.in_(body.case_ids))
    rows = q.order_by(ApiCase.id).all()
    cases = [
        {
            "name": r.name,
            "method": r.method,
            "path": r.path,
            "query": r.query_json if isinstance(r.query_json, dict) else {},
            "headers": r.headers_json if isinstance(r.headers_json, dict) else {},
            "body_type": r.body_type,
            "body": r.body_text or "",
        }
        for r in rows
    ]
    return export_postman_collection(name=body.name, cases=cases, base_url=body.base_url)
