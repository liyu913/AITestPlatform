"""R2: AI action bindings and resolution precedence."""

from fastapi.testclient import TestClient

from app.main import app
from app.models.ai.ai_config import AiSkillVersion
from app.models.ai.system_ai_settings import SystemLlmProfile
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _user(username: str, pw: str = "p") -> None:
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str = "p") -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str, name: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _profile(name: str, model: str, *, active: bool) -> int:
    db = TestingSessionLocal()
    try:
        row = SystemLlmProfile(
            name=name, api_base="https://llm.example.com/v1", model=model, is_active=active
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def _skill(key: str, *, action: str = "case_design", project_id: int | None = None) -> int:
    db = TestingSessionLocal()
    try:
        row = AiSkillVersion(
            project_id=project_id,
            skill_key=key,
            name=f"{key} skill",
            action=action,
            version=1,
            resource_text="# skill",
            enabled=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def test_resolution_precedence_and_overrides():
    _user("r2_ai")
    tok = _login("r2_ai")
    pid = _project(tok, "R2-AI")
    client = TestClient(app)
    h = _auth(tok)

    system_profile = _profile("system-default", "model-system", active=True)
    project_profile = _profile("project-default", "model-project", active=False)
    action_profile = _profile("action-model", "model-action", active=False)
    skill_id = _skill("case-design")

    # 1) Nothing configured: falls back to the active system profile.
    r = client.get(f"/api/v1/projects/{pid}/ai-config/resolve?action=case_design", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "system"
    assert r.json()["model_profile_id"] == system_profile

    # 2) Project default wins over the system default.
    r = client.put(
        f"/api/v1/projects/{pid}/ai-config/defaults",
        headers=h,
        json={"default_model_profile_id": project_profile},
    )
    assert r.status_code == 200, r.text
    resolved = client.get(
        f"/api/v1/projects/{pid}/ai-config/resolve?action=case_design", headers=h
    ).json()
    assert resolved["source"] == "project"
    assert resolved["model_profile_id"] == project_profile

    # 3) Action binding adds a mode and can pin its own model/skill.
    r = client.put(
        f"/api/v1/projects/{pid}/ai-config/bindings/case_design",
        headers=h,
        json={"mode": "skill", "skill_version_id": skill_id, "model_profile_id": action_profile},
    )
    assert r.status_code == 200, r.text
    resolved = client.get(
        f"/api/v1/projects/{pid}/ai-config/resolve?action=case_design", headers=h
    ).json()
    assert resolved["source"] == "action"
    assert resolved["mode"] == "skill"
    assert resolved["skill_version_id"] == skill_id
    assert resolved["skill_key"] == "case-design"
    assert resolved["model_name"] == "model-action"

    # 4) A per-call override wins over the action binding.
    resolved = client.get(
        f"/api/v1/projects/{pid}/ai-config/resolve"
        f"?action=case_design&mode=model&model_profile_id={project_profile}",
        headers=h,
    ).json()
    assert resolved["source"] == "call"
    assert resolved["mode"] == "model"
    assert resolved["skill_version_id"] is None
    assert resolved["model_profile_id"] == project_profile

    # 5) Skill mode without a skill is rejected.
    bad = client.put(
        f"/api/v1/projects/{pid}/ai-config/bindings/review",
        headers=h,
        json={"mode": "skill"},
    )
    assert bad.status_code == 400, bad.text


def test_binding_rejects_foreign_skill_and_unknown_action():
    _user("r2_iso")
    tok = _login("r2_iso")
    client = TestClient(app)
    h = _auth(tok)
    pid_a = _project(tok, "R2-ISO-A")
    pid_b = _project(tok, "R2-ISO-B")
    _profile("iso-system", "model-iso", active=True)
    foreign_skill = _skill("foreign", project_id=pid_a)

    r = client.put(
        f"/api/v1/projects/{pid_b}/ai-config/bindings/case_design",
        headers=h,
        json={"mode": "skill", "skill_version_id": foreign_skill},
    )
    assert r.status_code == 404, r.text

    bad = client.put(
        f"/api/v1/projects/{pid_b}/ai-config/bindings/not-an-action",
        headers=h,
        json={"mode": "model"},
    )
    assert bad.status_code == 400, bad.text

    # A disabled skill cannot be bound.
    db = TestingSessionLocal()
    try:
        skill = db.get(AiSkillVersion, foreign_skill)
        skill.project_id = pid_b
        skill.enabled = False
        db.commit()
    finally:
        db.close()
    disabled = client.put(
        f"/api/v1/projects/{pid_b}/ai-config/bindings/case_design",
        headers=h,
        json={"mode": "skill", "skill_version_id": foreign_skill},
    )
    assert disabled.status_code == 409, disabled.text

def test_llm_options_and_call_log_carry_resolved_config():
    from app.models.ai.ai_call_log import AiCallLog
    from app.services.ai import ai_config
    from app.services.ai.call_log import record_ai_call

    _user("r2_opts")
    tok = _login("r2_opts")
    pid = _project(tok, "R2-OPTS")
    client = TestClient(app)
    h = _auth(tok)
    _profile("opts-system", "model-system", active=True)
    bound = _profile("opts-bound", "model-bound", active=False)
    skill_id = _skill("opts-skill")

    client.put(
        f"/api/v1/projects/{pid}/ai-config/bindings/case_design",
        headers=h,
        json={"mode": "skill", "skill_version_id": skill_id, "model_profile_id": bound},
    )
    opts = client.get(f"/api/v1/projects/{pid}/ai/llm-options", headers=h)
    assert opts.status_code == 200, opts.text
    body = opts.json()
    assert body["mode"] == "skill"
    assert body["source"] == "action"
    assert body["skill_name"] == "opts-skill skill"
    assert body["skill_version"] == 1
    assert body["model_profile_id"] == bound
    assert body["default_model"] == "model-bound"

    from app.services.ai import call_context

    db = TestingSessionLocal()
    try:
        resolved = ai_config.resolve(db, project_id=pid, action="case_design")
    finally:
        db.close()
    project_token = call_context.project_id_var.set(pid)
    with ai_config.bind_context(resolved):
        record_ai_call(
            kind="llm",
            scene="用例生成",
            model="model-bound",
            status="success",
            http_status=200,
            latency_ms=12,
            prompt_tokens=3,
            completion_tokens=5,
            total_tokens=8,
            error_message=None,
            request_text="{}",
            response_text="{}",
        )
    call_context.project_id_var.reset(project_token)
    check = TestingSessionLocal()
    try:
        row = check.query(AiCallLog).order_by(AiCallLog.id.desc()).first()
        assert row is not None
        assert row.action == "case_design"
        assert row.config_source == "action"
        assert row.skill_version_id == skill_id
        assert (row.resolved_config or {}).get("mode") == "skill"
        assert (row.resolved_config or {}).get("model_profile_id") == bound
    finally:
        check.close()

    tasks = client.get(f"/api/v1/projects/{pid}/ai/design-tasks", headers=h)
    assert tasks.status_code == 200, tasks.text
    rows = tasks.json()
    assert rows and rows[0]["action"] == "case_design"
    assert rows[0]["config_source"] == "action"
    assert (rows[0]["resolved_config"] or {}).get("skill_version_id") == skill_id
