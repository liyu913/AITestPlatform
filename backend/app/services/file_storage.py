import re
import shutil
import uuid
from pathlib import Path

from app.config import settings


def _safe_filename(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w.\-]", "_", base, flags=re.UNICODE)
    return base or "upload"


def save_project_upload(project_id: int, filename: str, data: bytes) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/{safe_name}. Returns (relative_storage_key, size_bytes)."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    path = dest_dir / safe
    path.write_bytes(data)
    rel = f"{project_id}/{safe}"
    return rel, len(data)


def save_requirement_attachment(
    project_id: int, requirement_id: int, filename: str, data: bytes
) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/requirements/{requirement_id}/{uuid}_{safe_name}."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id) / "requirements" / str(requirement_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stored = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = dest_dir / stored
    path.write_bytes(data)
    rel = f"{project_id}/requirements/{requirement_id}/{stored}"
    return rel, len(data)


# Back-compat alias for older call sites / tests.
save_requirement_source = save_requirement_attachment


def save_ui_run_screenshot(
    project_id: int, run_id: int, filename: str, data: bytes
) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/ui-runs/{run_id}/{uuid}_{safe_name}."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id) / "ui-runs" / str(run_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stored = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = dest_dir / stored
    path.write_bytes(data)
    rel = f"{project_id}/ui-runs/{run_id}/{stored}"
    return rel, len(data)


def save_ui_explore_screenshot(
    project_id: int, session_id: int, filename: str, data: bytes
) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/ui-explore/{session_id}/{uuid}_{safe_name}."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id) / "ui-explore" / str(session_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stored = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = dest_dir / stored
    path.write_bytes(data)
    rel = f"{project_id}/ui-explore/{session_id}/{stored}"
    return rel, len(data)


def save_app_run_screenshot(
    project_id: int, run_id: int, filename: str, data: bytes
) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/app-runs/{run_id}/{uuid}_{safe_name}."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id) / "app-runs" / str(run_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stored = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = dest_dir / stored
    path.write_bytes(data)
    rel = f"{project_id}/app-runs/{run_id}/{stored}"
    return rel, len(data)


def save_execution_attachment(
    project_id: int, item_id: int, filename: str, data: bytes
) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/executions/{item_id}/{uuid}_{safe_name}."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id) / "executions" / str(item_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stored = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = dest_dir / stored
    path.write_bytes(data)
    rel = f"{project_id}/executions/{item_id}/{stored}"
    return rel, len(data)


def save_defect_attachment(
    project_id: int, defect_id: int, filename: str, data: bytes
) -> tuple[str, int]:
    """Save under upload_dir/{project_id}/defects/{defect_id}/{uuid}_{safe_name}."""
    root = Path(settings.upload_dir).resolve()
    dest_dir = root / str(project_id) / "defects" / str(defect_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    stored = f"{uuid.uuid4().hex[:12]}_{safe}"
    path = dest_dir / stored
    path.write_bytes(data)
    rel = f"{project_id}/defects/{defect_id}/{stored}"
    return rel, len(data)


def remove_storage_key(storage_key: str | None) -> None:
    if not storage_key:
        return
    path = absolute_path_for_storage_key(storage_key)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    parent = path.parent
    if parent.name.isdigit() and parent.parent.name == "requirements":
        try:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


def absolute_path_for_storage_key(storage_key: str) -> Path:
    return Path(settings.upload_dir).resolve() / storage_key


def remove_project_upload_dir(project_id: int) -> None:
    root = Path(settings.upload_dir).resolve() / str(project_id)
    shutil.rmtree(root, ignore_errors=True)
