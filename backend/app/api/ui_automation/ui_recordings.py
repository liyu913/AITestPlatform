from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_project_role
from app.models.identity.user import User
from app.models.project.project import ProjectMember
from app.schemas.ui_automation.ui_automation import UiRecordingOut, UiRecordingStart
from app.services.ui_automation.ui_recorder import get_recording, start_recording, stop_recording

router = APIRouter(prefix="/projects/{project_id}/ui-recordings", tags=["ui-recordings"])


def _out(session) -> UiRecordingOut:
    return UiRecordingOut.model_validate(session.to_dict())


@router.post("/", response_model=UiRecordingOut, status_code=201)
def start(
    project_id: int,
    body: UiRecordingStart,
    db: Session = Depends(get_db),
    _: ProjectMember = Depends(require_project_role("editor")),
    user: User = Depends(get_current_user),
) -> UiRecordingOut:
    session = start_recording(db, project_id=project_id, user_id=user.id, env_id=body.env_id)
    return _out(session)


@router.get("/{session_id}", response_model=UiRecordingOut)
def detail(
    project_id: int,
    session_id: str,
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiRecordingOut:
    return _out(get_recording(project_id, session_id))


@router.post("/{session_id}/stop", response_model=UiRecordingOut)
def stop(
    project_id: int,
    session_id: str,
    _: ProjectMember = Depends(require_project_role("editor")),
) -> UiRecordingOut:
    return _out(stop_recording(project_id, session_id))
