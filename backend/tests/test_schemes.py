"""R1: test schemes, plan references and formal publish pointer."""

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


def _scheme(client: TestClient, pid: int, h: dict, name: str = "支付测试方案") -> dict:
    r = client.post(
        f"/api/v1/projects/{pid}/schemes/",
        headers=h,
        json={
            "name": name,
            "scope": "支付主链路",
            "strategy": "风险优先",
            "risks": "第三方回调不稳定",
            "methods": "等价类 + 异常注入",
            "exit_criteria": "P0 全通过",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _case(client: TestClient, pid: int, h: dict, title: str = "下单成功") -> dict:
    r = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": title,
            "overall_expected": "生成订单",
            "steps": [{"order": 1, "action": "提交订单", "expected": "订单创建"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@patch("app.api.functional.testcases.enqueue_reindex")
def test_scheme_versions_and_plan_reference(_mock):
    _user("r1_owner")
    tok = _login("r1_owner")
    pid = _project(tok, "R1-SCHEME")
    client = TestClient(app)
    h = _auth(tok)

    scheme = _scheme(client, pid, h)
    assert scheme["current_version_no"] == 1
    assert scheme["latest_snapshot"]["scope"] == "支付主链路"
    version_1 = scheme["versions"][0]
    assert version_1["version_no"] == 1
    assert version_1["snapshot"]["exit_criteria"] == "P0 全通过"

    # A content edit produces a new immutable version.
    r = client.patch(
        f"/api/v1/projects/{pid}/schemes/{scheme['id']}",
        headers=h,
        json={"risks": "退款回调重复", "change_note": "补充退款风险"},
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["current_version_no"] == 2
    assert updated["versions"][-1]["snapshot"]["risks"] == "退款回调重复"
    versions = client.get(
        f"/api/v1/projects/{pid}/schemes/{scheme['id']}/versions", headers=h
    ).json()
    assert [v["version_no"] for v in versions] == [2, 1]

    # Plans pin a scheme version and expose it for display.
    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/",
        headers=h,
        json={"name": "1.2 回归计划", "scheme_version_id": version_1["id"]},
    )
    assert plan.status_code == 201, plan.text
    body = plan.json()
    assert body["scheme_id"] == scheme["id"]
    assert body["scheme_version_id"] == version_1["id"]
    assert body["scheme_name"] == "支付测试方案"
    assert body["scheme_version_no"] == 1

    # Editing the scheme does not change the plan's pinned version.
    plan_after = client.get(f"/api/v1/projects/{pid}/test-plans/{body['id']}", headers=h).json()
    assert plan_after["scheme_version_no"] == 1

    switched = client.patch(
        f"/api/v1/projects/{pid}/test-plans/{body['id']}",
        headers=h,
        json={"scheme_version_id": updated["versions"][-1]["id"]},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["scheme_version_no"] == 2

    # Cross-project references are rejected instead of leaking scheme data.
    other_pid = _project(tok, "R1-OTHER")
    r = client.post(
        f"/api/v1/projects/{other_pid}/test-plans/",
        headers=h,
        json={"name": "越权计划", "scheme_version_id": version_1["id"]},
    )
    assert r.status_code == 404, r.text


@patch("app.api.functional.testcases.enqueue_reindex")
def test_publish_requires_approved_version_and_is_idempotent(_mock):
    _user("r1_pub")
    tok = _login("r1_pub")
    pid = _project(tok, "R1-PUBLISH")
    client = TestClient(app)
    h = _auth(tok)
    case = _case(client, pid, h)

    # Draft versions cannot be published.
    r = client.post(f"/api/v1/projects/{pid}/test-cases/{case['id']}/publish", headers=h, json={})
    assert r.status_code == 409, r.text

    submitted = client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/submit-review", headers=h
    )
    assert submitted.status_code == 200, submitted.text
    queue = client.get(f"/api/v1/projects/{pid}/test-cases/review-queue", headers=h).json()
    assert [row["case_id"] for row in queue] == [case["id"]]

    approved = client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/review",
        headers=h,
        json={"decision": "approved", "comment": "符合预期"},
    )
    assert approved.status_code == 200, approved.text
    assert client.get(f"/api/v1/projects/{pid}/test-cases/review-queue", headers=h).json() == []

    published = client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/publish", headers=h, json={}
    )
    assert published.status_code == 200, published.text
    data = published.json()
    assert data["published_version_no"] == 1
    assert data["published_version_id"] is not None
    assert data["published_at"] is not None

    # Re-publishing the same version is a conflict, not a silent overwrite.
    again = client.post(
        f"/api/v1/projects/{pid}/test-cases/{case['id']}/publish", headers=h, json={}
    )
    assert again.status_code == 409, again.text


@patch("app.api.functional.testcases.enqueue_reindex")
def test_scheme_isolation_between_projects(_mock):
    _user("r1_iso")
    tok = _login("r1_iso")
    client = TestClient(app)
    h = _auth(tok)
    pid_a = _project(tok, "R1-A")
    pid_b = _project(tok, "R1-B")
    scheme = _scheme(client, pid_a, h)

    hidden = client.get(f"/api/v1/projects/{pid_b}/schemes/{scheme['id']}", headers=h)
    assert hidden.status_code == 404
    listing = client.get(f"/api/v1/projects/{pid_b}/schemes/", headers=h).json()
    assert listing["total"] == 0
