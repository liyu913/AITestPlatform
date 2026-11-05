"""Case lifecycle / version / review contract (B2)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
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
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _case(client: TestClient, pid: int, h: dict, **over) -> dict:
    payload = {
        "title": "登录成功",
        "overall_expected": "进入首页",
        "steps": [{"order": 1, "action": "输入账号密码", "expected": "表单可见"}],
    }
    payload.update(over)
    r = client.post(f"/api/v1/projects/{pid}/test-cases/", headers=h, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


@patch("app.api.functional.testcases.enqueue_reindex")
def test_lifecycle_split_and_legacy_payload(_mock):
    _user("lc_split")
    tok = _login("lc_split")
    pid = _project(tok, "LC1")
    client = TestClient(app)
    h = _auth(tok)

    draft = _case(client, pid, h)
    assert draft["lifecycle_status"] == "draft"
    assert draft["status"] == "draft"
    assert draft["last_result"] is None
    assert draft["version_no"] == 1

    # Old clients send "ready": mapped to a lifecycle state, not an execution result.
    ready = _case(client, pid, h, title="就绪用例", status="ready")
    assert ready["lifecycle_status"] == "confirmed"
    assert ready["last_result"] is None

    # Legacy import of an already-executed case splits into both axes.
    imported = _case(client, pid, h, title="历史失败用例", status="failed")
    assert imported["lifecycle_status"] == "confirmed"
    assert imported["last_result"] == "failed"

    # The API refuses to write an execution outcome through the lifecycle field.
    bad = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{draft['id']}", headers=h, json={"status": "passed"}
    )
    assert bad.status_code == 400
    assert "生命周期" in bad.json()["message"]

    bad2 = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{draft['id']}", headers=h, json={"status": "unknown-xyz"}
    )
    assert bad2.status_code == 400

    ok = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{draft['id']}",
        headers=h,
        json={"lifecycle_status": "archived"},
    )
    assert ok.status_code == 200
    assert ok.json()["lifecycle_status"] == "archived"
    assert ok.json()["archived_at"] is not None


@patch("app.api.functional.testcases.enqueue_reindex")
def test_review_flow_binds_versions(_mock):
    _user("lc_review")
    tok = _login("lc_review")
    pid = _project(tok, "LC2")
    client = TestClient(app)
    h = _auth(tok)

    case = _case(client, pid, h)
    cid = case["id"]

    versions = client.get(f"/api/v1/projects/{pid}/test-cases/{cid}/versions", headers=h)
    assert versions.status_code == 200
    assert versions.json()[0]["version_no"] == 1
    assert versions.json()[0]["change_source"] == "create"
    assert versions.json()[0]["snapshot"]["steps"][0]["expected"] == "表单可见"

    # Editing stores a new immutable version.
    edit = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers=h,
        json={"title": "登录成功（改）", "change_note": "补充步骤"},
    )
    assert edit.status_code == 200
    assert edit.json()["version_no"] == 2
    versions = client.get(f"/api/v1/projects/{pid}/test-cases/{cid}/versions", headers=h).json()
    assert [v["version_no"] for v in versions] == [2, 1]
    assert versions[0]["change_note"] == "补充步骤"
    # v1 keeps the old title, so old runs still describe what they ran.
    assert versions[1]["snapshot"]["title"] == "登录成功"

    # Review requires a submitted case and binds its decision to a version.
    sub = client.post(f"/api/v1/projects/{pid}/test-cases/{cid}/submit-review", headers=h)
    assert sub.status_code == 200
    assert sub.json()["lifecycle_status"] == "pending_review"

    reject_no_comment = client.post(
        f"/api/v1/projects/{pid}/test-cases/{cid}/review",
        headers=h,
        json={"decision": "rejected"},
    )
    assert reject_no_comment.status_code == 400

    reject = client.post(
        f"/api/v1/projects/{pid}/test-cases/{cid}/review",
        headers=h,
        json={"decision": "rejected", "comment": "预期不可观察"},
    )
    assert reject.status_code == 200
    assert reject.json()["lifecycle_status"] == "draft"

    client.post(f"/api/v1/projects/{pid}/test-cases/{cid}/submit-review", headers=h)
    approve = client.post(
        f"/api/v1/projects/{pid}/test-cases/{cid}/review",
        headers=h,
        json={"decision": "approved", "comment": "通过"},
    )
    assert approve.status_code == 200
    assert approve.json()["lifecycle_status"] == "confirmed"

    reviews = client.get(f"/api/v1/projects/{pid}/test-cases/{cid}/reviews", headers=h).json()
    assert len(reviews) == 2
    assert reviews[0]["decision"] == "approved"
    assert reviews[0]["version_no"] >= 1

    # Editing a confirmed case sends it back to review instead of silently
    # keeping a stale approval.
    edited = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}", headers=h, json={"overall_expected": "进入首页并显示昵称"}
    )
    assert edited.status_code == 200
    assert edited.json()["lifecycle_status"] == "pending_review"


@patch("app.api.functional.testcases.enqueue_reindex")
def test_archive_protects_history_and_delete_guard(_mock):
    _user("lc_arch")
    tok = _login("lc_arch")
    pid = _project(tok, "LC3")
    client = TestClient(app)
    h = _auth(tok)

    referenced = _case(client, pid, h, title="被引用用例")
    me = client.get("/api/v1/users/me", headers=h).json()["id"]

    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划 A"}
    ).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "第 1 轮", "status": "active"},
    ).json()
    client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [referenced["id"]]},
    )
    client.post(
        f"/api/v1/projects/{pid}/defects/",
        headers=h,
        json={"title": "登录异常", "case_id": referenced["id"], "assignee_id": me},
    )
    client.post(
        f"/api/v1/projects/{pid}/test-cases/{referenced['id']}/submit-review", headers=h
    )

    # Referenced by a defect/cycle item: hard delete is refused.
    blocked = client.delete(f"/api/v1/projects/{pid}/test-cases/{referenced['id']}", headers=h)
    assert blocked.status_code == 409
    assert "归档" in blocked.json()["message"]

    archived = client.post(
        f"/api/v1/projects/{pid}/test-cases/{referenced['id']}/archive",
        headers=h,
        json={"reason": "需求下线"},
    )
    assert archived.status_code == 200
    assert archived.json()["lifecycle_status"] == "archived"

    # Archived assets leave the default list but stay reachable explicitly.
    default_list = client.get(f"/api/v1/projects/{pid}/test-cases/", headers=h).json()
    assert all(i["id"] != referenced["id"] for i in default_list["items"])
    explicit = client.get(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        params={"lifecycle_status": "archived"},
    ).json()
    assert any(i["id"] == referenced["id"] for i in explicit["items"])

    # Archived cases are read-only until restored.
    readonly = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{referenced['id']}", headers=h, json={"title": "改"}
    )
    assert readonly.status_code == 409

    restored = client.post(
        f"/api/v1/projects/{pid}/test-cases/{referenced['id']}/restore", headers=h
    )
    assert restored.status_code == 200
    assert restored.json()["lifecycle_status"] == "draft"

    # An unreferenced case can still be deleted outright.
    free = _case(client, pid, h, title="未引用用例")
    gone = client.delete(f"/api/v1/projects/{pid}/test-cases/{free['id']}", headers=h)
    assert gone.status_code == 204


@patch("app.api.functional.testcases.enqueue_reindex")
def test_requirement_acceptance_rules_and_versions(_mock):
    _user("lc_req")
    tok = _login("lc_req")
    pid = _project(tok, "LC4")
    client = TestClient(app)
    h = _auth(tok)

    req = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "登录需求", "body_md": "登录", "acceptance_rules": "1. 正确密码可登录"},
    )
    assert req.status_code == 201
    body = req.json()
    assert body["acceptance_rules"].startswith("1.")
    assert body["version_no"] == 1

    v1 = client.get(f"/api/v1/projects/{pid}/requirements/{body['id']}/versions", headers=h)
    assert v1.status_code == 200
    assert len(v1.json()) == 1
    assert v1.json()[0]["snapshot"]["acceptance_rules"].startswith("1.")

    upd = client.patch(
        f"/api/v1/projects/{pid}/requirements/{body['id']}",
        headers=h,
        json={"acceptance_rules": "1. 正确密码可登录\n2. 错误密码提示", "change_note": "补充失败路径"},
    )
    assert upd.status_code == 200
    assert upd.json()["version_no"] == 2

    versions = client.get(f"/api/v1/projects/{pid}/requirements/{body['id']}/versions", headers=h).json()
    assert [v["version_no"] for v in versions] == [2, 1]
    assert versions[0]["change_note"] == "补充失败路径"
    assert "错误密码" in versions[0]["snapshot"]["acceptance_rules"]
    assert "错误密码" not in versions[1]["snapshot"]["acceptance_rules"]
