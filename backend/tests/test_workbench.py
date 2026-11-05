"""Workbench buckets are derived from real records and disappear with them (B1)."""

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


@patch("app.api.functional.testcases.enqueue_reindex")
def test_workbench_lists_actionable_records(_mock):
    _user("wb_user")
    tok = _login("wb_user")
    pid = _project(tok, "WB1")
    client = TestClient(app)
    h = _auth(tok)
    me = client.get("/api/v1/users/me", headers=h).json()["id"]

    empty = client.get(f"/api/v1/projects/{pid}/workbench", headers=h)
    assert empty.status_code == 200
    body = empty.json()
    assert [b["key"] for b in body["buckets"]] == [
        "pending_run",
        "pending_review",
        "pending_regression",
    ]
    assert all(b["count"] == 0 for b in body["buckets"])
    assert body["recent_runs"] == []

    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "下单流程",
            "steps": [{"order": 1, "action": "提交订单", "expected": "订单创建成功"}],
        },
    ).json()

    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "迭代 7"}
    ).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "第 1 轮", "status": "active"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case["id"]]},
    ).json()
    client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/assign",
        headers=h,
        json={"item_ids": [items[0]["id"]], "assignee_id": me},
    )

    client.post(f"/api/v1/projects/{pid}/test-cases/{case['id']}/submit-review", headers=h)

    defect = client.post(
        f"/api/v1/projects/{pid}/defects/",
        headers=h,
        json={
            "title": "提交订单后无响应",
            "case_id": case["id"],
            "assignee_id": me,
            "status": "resolved",
        },
    )
    assert defect.status_code == 201

    body = client.get(f"/api/v1/projects/{pid}/workbench", headers=h).json()
    by_key = {b["key"]: b for b in body["buckets"]}

    run_bucket = by_key["pending_run"]
    assert run_bucket["count"] == 1
    task = run_bucket["items"][0]
    assert task["kind"] == "manual_run"
    assert task["title"] == "下单流程"
    assert task["link"] == f"cycles/{cycle['id']}?item={items[0]['id']}"

    review_bucket = by_key["pending_review"]
    assert review_bucket["count"] == 1
    assert review_bucket["items"][0]["target_id"] == case["id"]
    assert review_bucket["items"][0]["link"].startswith("test-cases?status=pending_review")

    regression_bucket = by_key["pending_regression"]
    assert regression_bucket["count"] == 1
    assert regression_bucket["items"][0]["link"] == f"defects?defect={defect.json()['id']}"

    # Executing the assigned item removes it from the "to run" bucket.
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{items[0]['id']}",
        headers=h,
        json={"result": "passed", "actual_result": "订单创建成功"},
    )
    body = client.get(f"/api/v1/projects/{pid}/workbench", headers=h).json()
    by_key = {b["key"]: b for b in body["buckets"]}
    assert by_key["pending_run"]["count"] == 0
    # The manual result is projected onto the case without touching its lifecycle.
    after = client.get(f"/api/v1/projects/{pid}/test-cases/{case['id']}", headers=h).json()
    assert after["last_result"] == "passed"
    assert after["lifecycle_status"] == "pending_review"
    assert isinstance(body["recent_runs"], list)


def test_workbench_requires_project_membership():
    _user("wb_owner")
    _user("wb_outsider")
    owner_tok = _login("wb_owner")
    outsider_tok = _login("wb_outsider")
    pid = _project(owner_tok, "WB2")

    denied = TestClient(app).get(
        f"/api/v1/projects/{pid}/workbench", headers=_auth(outsider_tok)
    )
    assert denied.status_code in (403, 404)
