"""B5/B7: defect evidence panel and batch-item execution from the plan page."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _task_stub() -> MagicMock:
    m = MagicMock()
    m.id = "task-test"
    return m


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


@patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay", return_value=_task_stub())
@patch("app.api.functional.testcases.enqueue_reindex")
def test_defect_evidence_shows_original_failure_and_retest(_mock, _delay):
    _user("b5_evidence")
    tok = _login("b5_evidence")
    pid = _project(tok, "B5EV")
    client = TestClient(app)
    h = _auth(tok)

    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "预发", "base_url": "https://api.example.com"},
    ).json()
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "查询订单",
            "overall_expected": "返回订单详情",
            "steps": [{"order": 1, "action": "请求接口", "expected": "状态码 200"}],
        },
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={
            "name": "批次",
            "status": "active",
            "target_build": "build-1",
            "env_kind": "api",
            "env_id": env["id"],
        },
    ).json()
    item = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case["id"]]},
    ).json()[0]
    att = f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{item['id']}/attempts"

    client.post(
        att,
        headers=h,
        json={"execution_type": "manual", "status": "failed", "summary": "第一次：接口 500"},
    )
    # A defect can only be filed while the item is failed.
    defect = client.post(
        f"/api/v1/projects/{pid}/defects/from-cycle-item/{item['id']}", headers=h
    )
    assert defect.status_code == 201, defect.text
    defect_id = defect.json()["id"]

    # Retry to pass afterwards: the attempt history keeps both outcomes.
    client.post(
        att,
        headers=h,
        json={"execution_type": "manual", "status": "passed", "summary": "重试：200"},
    )

    evidence = client.get(f"/api/v1/projects/{pid}/defects/{defect_id}/evidence", headers=h)
    assert evidence.status_code == 200, evidence.text
    body = evidence.json()
    assert body["cycle"]["name"] == "批次"
    assert body["cycle"]["plan_name"] == "计划"
    assert body["cycle"]["target_build"] == "build-1"
    assert body["cycle"]["environment"] == "预发"
    assert body["item"]["asset_title"] == "查询订单"
    assert body["item"]["attempt_no"] == 2
    assert [a["attempt_no"] for a in body["attempts"]] == [1, 2]
    assert [a["status"] for a in body["attempts"]] == ["failed", "passed"]
    assert body["attempts"][0]["summary"].startswith("第一次")
    assert body["history"][0]["to_status"] == "open"

    # Retest and reopen are visible in the same evidence payload.
    client.post(
        f"/api/v1/projects/{pid}/defects/{defect_id}/verify",
        headers=h,
        json={"result": "passed", "fix_build": "build-2", "note": "已修复"},
    )
    after = client.get(f"/api/v1/projects/{pid}/defects/{defect_id}/evidence", headers=h).json()
    assert after["verification_result"] == "passed"
    assert after["verification_note"] == "已修复"
    assert [h["to_status"] for h in after["history"]][-1] == "resolved"

    # Cross-project evidence lookup is a 404.
    other = _project(tok, "B5EV2")
    assert (
        client.get(f"/api/v1/projects/{other}/defects/{defect_id}/evidence", headers=h).status_code
        == 404
    )


@patch("app.tasks.ui_automation.run_ui_case.run_ui_case_task.apply_async", return_value=_task_stub())
@patch("app.tasks.api_automation.run_api_case.run_api_case_task.delay", return_value=_task_stub())
@patch("app.api.functional.testcases.enqueue_reindex")
def test_batch_item_exposes_enough_context_to_start_a_run(_mock, _delay, _apply):
    """The plan page starts a run with the item's asset id + batch environment."""
    _user("b4_start")
    tok = _login("b4_start")
    pid = _project(tok, "B4START")
    client = TestClient(app)
    h = _auth(tok)

    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "接口环境", "base_url": "https://api.example.com"},
    ).json()
    api_case = client.post(
        f"/api/v1/projects/{pid}/api-cases/", headers=h, json={"name": "接口用例", "path": "/x"}
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={
            "name": "批次",
            "status": "active",
            "target_build": "build-9",
            "env_kind": "api",
            "env_id": env["id"],
        },
    ).json()
    item = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "api_case", "asset_id": api_case["id"]}]},
    ).json()[0]

    # Batch response carries env/build/asset info the page needs.
    got = client.get(f"/api/v1/projects/{pid}/cycles/{cycle['id']}", headers=h).json()
    assert got["env_kind"] == "api"
    assert got["env_id"] == env["id"]
    assert got["target_build"] == "build-9"
    assert item["asset_type"] == "api_case"
    assert item["asset_id"] == api_case["id"]

    # Starting the run with that context links it to the batch item (B4 contract).
    run = client.post(
        f"/api/v1/projects/{pid}/api-cases/{item['asset_id']}/run",
        headers=h,
        json={"cycle_item_id": item["id"], "env_id": got["env_id"], "sync": False},
    )
    assert run.status_code == 201, run.text
    assert run.json()["id"] > 0
