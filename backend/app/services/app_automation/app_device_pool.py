"""Claim / release devices from the APP device pool."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.datetime_json import now_beijing, to_beijing_iso
from app.models.app_automation.app_automation import AppDevice, AppEnvironment, AppRun


def _utc_now_iso() -> str:
    return to_beijing_iso(now_beijing())


def device_snapshot(device: AppDevice) -> dict:
    return {
        "id": device.id,
        "name": device.name,
        "platform": device.platform,
        "udid": device.udid or "",
        "device_name": device.device_name or "",
        "platform_version": device.platform_version or "",
        "appium_server_url": device.appium_server_url or "",
        "automation_name": device.automation_name or "",
        "tags": device.tags_json if isinstance(device.tags_json, list) else [],
        "worker_id": device.worker_id or "",
    }


def _tags_match(device_tags: list | None, required: list[str]) -> bool:
    if not required:
        return True
    have = {str(t).strip().lower() for t in (device_tags or []) if str(t).strip()}
    need = {str(t).strip().lower() for t in required if str(t).strip()}
    return need.issubset(have)


def claim_device(
    db: Session,
    *,
    project_id: int,
    platform: str,
    device_id: int | None = None,
    required_tags: list[str] | None = None,
    run_id: int | None = None,
) -> AppDevice | None:
    """Atomically claim an online device. Returns None if none available."""
    tags = [str(t) for t in (required_tags or []) if str(t).strip()]

    if device_id is not None:
        device = (
            db.query(AppDevice)
            .filter(AppDevice.id == device_id, AppDevice.project_id == project_id)
            .with_for_update()
            .first()
        )
        if device is None:
            return None
        if device.status not in ("online",):
            return None
        if (device.platform or "android").lower() != (platform or "android").lower():
            return None
        if not _tags_match(device.tags_json if isinstance(device.tags_json, list) else [], tags):
            return None
        device.status = "busy"
        device.current_run_id = run_id
        db.flush()
        return device

    q = (
        db.query(AppDevice)
        .filter(
            AppDevice.project_id == project_id,
            AppDevice.status == "online",
            AppDevice.platform == (platform or "android"),
        )
        .order_by(AppDevice.id)
        .with_for_update()
    )
    for device in q.all():
        if not _tags_match(device.tags_json if isinstance(device.tags_json, list) else [], tags):
            continue
        device.status = "busy"
        device.current_run_id = run_id
        db.flush()
        return device
    return None


def release_device(db: Session, device_id: int | None, *, run_id: int | None = None) -> None:
    if device_id is None:
        return
    device = db.get(AppDevice, device_id)
    if device is None:
        return
    if run_id is not None and device.current_run_id not in (None, run_id):
        return
    if device.status == "busy":
        device.status = "online"
    device.current_run_id = None
    db.flush()


def resolve_required_tags(run: AppRun, env: AppEnvironment | None) -> list[str]:
    if isinstance(run.device_tags_json, list) and run.device_tags_json:
        return [str(t) for t in run.device_tags_json if str(t).strip()]
    if env and isinstance(env.device_tags_json, list) and env.device_tags_json:
        return [str(t) for t in env.device_tags_json if str(t).strip()]
    return []


def merge_env_with_device(env_dict: dict, device: AppDevice | None) -> dict:
    """Overlay claimed device fields onto env caps for the runner."""
    out = dict(env_dict)
    if device is None:
        return out
    if device.udid:
        out["udid"] = device.udid
    if device.device_name:
        out["device_name"] = device.device_name
    if device.platform_version:
        out["platform_version"] = device.platform_version
    if device.appium_server_url:
        out["appium_server_url"] = device.appium_server_url
    if device.automation_name:
        out["automation_name"] = device.automation_name
    if device.platform:
        out["platform"] = device.platform
    return out


def heartbeat_device(
    db: Session,
    device: AppDevice,
    *,
    worker_id: str | None = None,
    status: str | None = None,
) -> AppDevice:
    device.last_heartbeat_at = _utc_now_iso()
    if worker_id is not None:
        device.worker_id = worker_id.strip()
    if status in ("online", "offline", "disabled"):
        # Do not allow heartbeat to steal a busy device into online while running
        if device.status == "busy" and status == "online":
            pass
        elif device.status != "busy" or status == "disabled":
            device.status = status
        elif status == "offline":
            device.status = "offline"
            device.current_run_id = None
    elif device.status in ("offline", ""):
        device.status = "online"
    db.flush()
    return device
