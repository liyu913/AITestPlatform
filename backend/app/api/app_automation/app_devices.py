from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.errors import AppError
from app.models.app_automation.app_automation import AppDevice
from app.models.project.project import ProjectMember
from app.models.identity.user import User
from app.schemas.app_automation.app_automation import (
    AppDeviceCreate,
    AppDeviceHeartbeatRequest,
    AppDeviceOut,
    AppDeviceScanOut,
    AppDeviceScanRequest,
    AppDeviceUpdate,
)
from app.schemas.page import Page
from app.services.app_automation.app_adb_scan import AdbCommandError, AdbNotFoundError, sync_adb_devices_to_pool
from app.services.app_automation.app_device_pool import heartbeat_device, release_device
from app.user_messages import (
    APP_ADB_NOT_FOUND,
    APP_ADB_SCAN_FAILED,
    APP_DEVICE_BUSY,
    APP_DEVICE_NOT_FOUND,
)
router = APIRouter(prefix="/projects/{project_id}/app-devices", tags=["app-devices"])


def _tags(row: AppDevice) -> list[str]:
    if isinstance(row.tags_json, list):
        return [str(t) for t in row.tags_json]
    return []


def _device_out(row: AppDevice) -> AppDeviceOut:
    return AppDeviceOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        platform=row.platform or "android",
        udid=row.udid or "",
        device_name=row.device_name or "",
        platform_version=row.platform_version or "",
        appium_server_url=row.appium_server_url or "",
        automation_name=row.automation_name or "",
        tags=_tags(row),
        status=row.status or "offline",
        worker_id=row.worker_id or "",
        last_heartbeat_at=row.last_heartbeat_at,
        current_run_id=row.current_run_id,
        notes=row.notes or "",
        creator_id=row.creator_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require(db: Session, project_id: int, device_id: int) -> AppDevice:
    row = (
        db.query(AppDevice)
        .filter(AppDevice.id == device_id, AppDevice.project_id == project_id)
        .first()
    )
    if row is None:
        raise AppError("not_found", APP_DEVICE_NOT_FOUND, status_code=404)
    return row


@router.get("/", response_model=Page[AppDeviceOut])
def list_devices(
    project_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, max_length=32),
    platform: str | None = Query(None, max_length=32),
    tag: str | None = Query(None, max_length=64),
    search: str | None = Query(None, max_length=256),
) -> Page[AppDeviceOut]:
    p, ps = max(1, page), min(200, max(1, page_size))
    q = db.query(AppDevice).filter(AppDevice.project_id == project_id)
    if status:
        q = q.filter(AppDevice.status == status)
    if platform:
        q = q.filter(AppDevice.platform == platform)
    if search and search.strip():
        s = f"%{search.strip()}%"
        q = q.filter(or_(AppDevice.name.like(s), AppDevice.udid.like(s)))
    rows = q.order_by(AppDevice.updated_at.desc(), AppDevice.id.desc()).all()
    if tag and tag.strip():
        needle = tag.strip().lower()
        rows = [
            r
            for r in rows
            if isinstance(r.tags_json, list)
            and any(str(t).strip().lower() == needle for t in r.tags_json)
        ]
    total = len(rows)
    page_rows = rows[(p - 1) * ps : (p - 1) * ps + ps]
    return Page[AppDeviceOut](
        items=[_device_out(r) for r in page_rows],
        total=total,
        page=p,
        page_size=ps,
    )


@router.post("/scan", response_model=AppDeviceScanOut)
def scan_devices(
    project_id: int,
    body: AppDeviceScanRequest | None = None,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AppDeviceScanOut:
    """Manually scan local adb devices and sync into the project device pool."""
    req = body or AppDeviceScanRequest()
    try:
        result = sync_adb_devices_to_pool(
            db,
            project_id=project_id,
            creator_id=user.id,
            mark_missing_offline=req.mark_missing_offline,
        )
    except AdbNotFoundError as ex:
        raise AppError("bad_request", APP_ADB_NOT_FOUND, detail=str(ex), status_code=400) from ex
    except AdbCommandError as ex:
        raise AppError("bad_request", APP_ADB_SCAN_FAILED, detail=str(ex), status_code=400) from ex
    return AppDeviceScanOut(
        scanned=result["scanned"],
        ready=result["ready"],
        created=result["created"],
        updated=result["updated"],
        online=result["online"],
        skipped_busy=result["skipped_busy"],
        marked_offline=result["marked_offline"],
        devices=[_device_out(r) for r in result["devices"]],
        raw=result["raw"],
    )


@router.post("/", response_model=AppDeviceOut, status_code=201)
def create_device(
    project_id: int,
    body: AppDeviceCreate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> AppDeviceOut:
    row = AppDevice(
        project_id=project_id,
        name=body.name.strip(),
        platform=body.platform,
        udid=body.udid or "",
        device_name=body.device_name or "",
        platform_version=body.platform_version or "",
        appium_server_url=body.appium_server_url or "",
        automation_name=body.automation_name or "",
        tags_json=list(body.tags or []),
        status=body.status,
        worker_id=body.worker_id or "",
        notes=body.notes or "",
        creator_id=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _device_out(row)


@router.get("/{device_id}", response_model=AppDeviceOut)
def get_device(
    project_id: int,
    device_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("viewer")),
) -> AppDeviceOut:
    return _device_out(_require(db, project_id, device_id))


@router.patch("/{device_id}", response_model=AppDeviceOut)
def update_device(
    project_id: int,
    device_id: int,
    body: AppDeviceUpdate,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppDeviceOut:
    row = _require(db, project_id, device_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        row.name = str(data["name"]).strip()
    for field in (
        "platform",
        "udid",
        "device_name",
        "platform_version",
        "appium_server_url",
        "automation_name",
        "worker_id",
        "notes",
    ):
        if field in data and data[field] is not None:
            setattr(row, field, data[field])
    if "tags" in data:
        row.tags_json = data["tags"] or []
    if "status" in data and data["status"] is not None:
        new_status = data["status"]
        if row.status == "busy" and new_status == "online":
            raise AppError("conflict", APP_DEVICE_BUSY, status_code=409)
        row.status = new_status
        if new_status in ("online", "offline", "disabled"):
            if new_status != "busy":
                row.current_run_id = None
    db.commit()
    db.refresh(row)
    return _device_out(row)


@router.delete("/{device_id}", status_code=204)
def delete_device(
    project_id: int,
    device_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("admin")),
) -> None:
    row = _require(db, project_id, device_id)
    if row.status == "busy":
        raise AppError("conflict", APP_DEVICE_BUSY, status_code=409)
    db.delete(row)
    db.commit()


@router.post("/{device_id}/heartbeat", response_model=AppDeviceOut)
def device_heartbeat(
    project_id: int,
    device_id: int,
    body: AppDeviceHeartbeatRequest,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppDeviceOut:
    row = _require(db, project_id, device_id)
    heartbeat_device(db, row, worker_id=body.worker_id, status=body.status)
    db.commit()
    db.refresh(row)
    return _device_out(row)


@router.post("/{device_id}/release", response_model=AppDeviceOut)
def device_release(
    project_id: int,
    device_id: int,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
) -> AppDeviceOut:
    row = _require(db, project_id, device_id)
    release_device(db, row.id)
    if row.status == "busy":
        row.status = "online"
        row.current_run_id = None
    db.commit()
    db.refresh(row)
    return _device_out(row)
