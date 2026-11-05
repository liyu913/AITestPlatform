"""R2: SKILL.md import and skill version management."""

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal

SKILL_MD = """---
name: review-checker
description: 检查用例步骤与预期质量
---

# 评审检查 Skill
逐条检查步骤是否可执行、预期是否可验证。
"""


def _user(username: str, *, superuser: bool = False, pw: str = "p") -> None:
    db = TestingSessionLocal()
    try:
        db.add(
            User(
                username=username,
                password_hash=auth_service.hash_password(pw),
                is_active=True,
                is_superuser=superuser,
            )
        )
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str = "p") -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_skill_import_versions_and_disable():
    _user("r2_skill", superuser=True)
    tok = _login("r2_skill")
    client = TestClient(app)
    h = _auth(tok)

    first = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=h,
        files={"file": ("SKILL.md", SKILL_MD, "text/markdown")},
        data={"action": "review"},
    )
    assert first.status_code == 201, first.text
    skill = first.json()
    assert skill["skill_key"] == "review-checker"
    assert skill["name"] == "review-checker"
    assert skill["description"] == "检查用例步骤与预期质量"
    assert skill["version"] == 1
    assert skill["enabled"] is True

    second = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=h,
        files={"file": ("SKILL.md", SKILL_MD, "text/markdown")},
        data={"action": "review", "skill_key": "review-checker"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2

    duplicate = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=h,
        files={"file": ("SKILL.md", SKILL_MD, "text/markdown")},
        data={"action": "review", "skill_key": "review-checker", "version": "2"},
    )
    assert duplicate.status_code == 409, duplicate.text

    bad_action = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=h,
        files={"file": ("x.md", "# x", "text/markdown")},
        data={"action": "nope"},
    )
    assert bad_action.status_code == 400, bad_action.text

    bad_key = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=h,
        files={"file": ("x.md", "# x", "text/markdown")},
        data={"action": "review", "skill_key": "Bad Key!"},
    )
    assert bad_key.status_code == 400, bad_key.text

    listing = client.get("/api/v1/admin/ai-skills/", headers=h).json()
    assert {s["version"] for s in listing if s["skill_key"] == "review-checker"} == {1, 2}

    disabled = client.patch(
        f"/api/v1/admin/ai-skills/{skill['id']}", headers=h, json={"enabled": False}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False


def test_skill_import_requires_superuser_and_disabled_skill_cannot_bind():
    _user("r2_regular")
    tok = _login("r2_regular")
    client = TestClient(app)
    h = _auth(tok)

    denied = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=h,
        files={"file": ("SKILL.md", SKILL_MD, "text/markdown")},
        data={"action": "review"},
    )
    assert denied.status_code == 403, denied.text

    project = client.post(
        "/api/v1/projects/", headers=h, json={"name": "R2-SKILL"}
    ).json()
    _user("r2_admin", superuser=True)
    admin_tok = _login("r2_admin")
    admin_h = _auth(admin_tok)
    imported = client.post(
        "/api/v1/admin/ai-skills/import",
        headers=admin_h,
        files={"file": ("SKILL.md", SKILL_MD, "text/markdown")},
        data={"action": "review", "skill_key": "review-off"},
    )
    assert imported.status_code == 201, imported.text
    skill_id = imported.json()["id"]
    off = client.patch(
        f"/api/v1/admin/ai-skills/{skill_id}", headers=admin_h, json={"enabled": False}
    )
    assert off.status_code == 200

    binding = client.put(
        f"/api/v1/projects/{project['id']}/ai-config/bindings/case_design",
        headers=h,
        json={"mode": "skill", "skill_version_id": skill_id},
    )
    assert binding.status_code == 409, binding.text
