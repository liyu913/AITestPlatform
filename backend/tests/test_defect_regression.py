"""B5: defect classification, fix build, retest and regression-gated closing."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.functional.defect_workflow import classify_failure
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


def _failed_item(client: TestClient, pid: int, h: dict, *, actual: str = "按钮点击后无响应") -> tuple[int, int, int]:
    """Create a plan/cycle with one failed manual item; return (cycle_id, item_id, case_id)."""
    env = client.post(
        f"/api/v1/projects/{pid}/ui-envs/",
        headers=h,
        json={"name": "预发环境", "base_url": "https://pre.example.com"},
    ).json()
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "提交订单",
            "overall_expected": "订单创建成功",
            "steps": [{"order": 1, "action": "点击提交", "expected": "显示订单号"}],
        },
    ).json()
    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "回归计划", "version": "1.2.0"}
    ).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "第 3 批", "status": "active", "target_build": "build-2026.11.05", "env_kind": "ui", "env_id": env["id"]},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case["id"]]},
    ).json()
    item_id = items[0]["id"]
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{item_id}",
        headers=h,
        json={"result": "failed", "actual_result": actual},
    )
    return cycle["id"], item_id, case["id"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_suggestion_prefills_and_blocks_duplicate(_mock):
    _user("b5_suggest")
    tok = _login("b5_suggest")
    pid = _project(tok, "B5A")
    client = TestClient(app)
    h = _auth(tok)
    cycle_id, item_id, case_id = _failed_item(client, pid, h, actual="连接超时，环境不可达")

    suggestion = client.get(
        f"/api/v1/projects/{pid}/defects/suggestion/from-cycle-item/{item_id}", headers=h
    )
    assert suggestion.status_code == 200, suggestion.text
    draft = suggestion.json()
    assert draft["category"] == "environment"  # classified from the failure text
    assert draft["expected_result"] == "订单创建成功"
    assert draft["actual_result"].startswith("连接超时")
    assert draft["environment"] == "预发环境"
    assert draft["found_build"] == "build-2026.11.05"
    assert draft["title"].startswith("[失败]")

    created = client.post(f"/api/v1/projects/{pid}/defects/from-cycle-item/{item_id}", headers=h)
    assert created.status_code == 201, created.text
    defect = created.json()
    assert defect["case_id"] == case_id
    assert defect["cycle_item_id"] == item_id
    assert defect["failure_source_type"] == "cycle_item"
    assert defect["failure_source_id"] == item_id
    assert defect["category"] == "environment"
    assert defect["expected_result"] == "订单创建成功"
    assert defect["found_build"] == "build-2026.11.05"
    assert defect["status"] == "open"
    assert defect["cycle_item_id"] == item_id
    assert defect["attachments"] == []

    # Same failure twice is refused with a pointer to the existing defect.
    again = client.post(f"/api/v1/projects/{pid}/defects/from-cycle-item/{item_id}", headers=h)
    assert again.status_code == 409
    assert str(defect["id"]) in again.json()["message"]

    history = client.get(f"/api/v1/projects/{pid}/defects/{defect['id']}/history", headers=h).json()
    assert len(history) == 1
    assert history[0]["to_status"] == "open"


@patch("app.api.functional.testcases.enqueue_reindex")
def test_close_requires_fix_build_and_passed_retest(_mock):
    _user("b5_close")
    tok = _login("b5_close")
    pid = _project(tok, "B5B")
    client = TestClient(app)
    h = _auth(tok)
    _, item_id, _ = _failed_item(client, pid, h)
    defect = client.post(f"/api/v1/projects/{pid}/defects/from-cycle-item/{item_id}", headers=h).json()
    base = f"/api/v1/projects/{pid}/defects/{defect['id']}"

    blockers = client.get(base + "/close-blockers", headers=h).json()
    assert any("修复构建" in b for b in blockers), blockers
    assert any("复测" in b for b in blockers), blockers

    early = client.patch(base, headers=h, json={"status": "closed"})
    assert early.status_code == 400
    assert "关闭" in early.json()["message"]

    # Failed retest reopens and clears the close attempt.
    failed = client.post(
        base + "/verify",
        headers=h,
        json={"result": "failed", "fix_build": "build-2026.11.10", "note": "仍然无响应"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "reopened"
    assert failed.json()["verification_result"] == "failed"
    assert failed.json()["closed_at"] is None

    passed = client.post(
        base + "/verify",
        headers=h,
        json={"result": "passed", "fix_build": "build-2026.11.10", "note": "订单号正常显示"},
    )
    assert passed.status_code == 200
    assert passed.json()["status"] == "resolved"
    assert passed.json()["fix_build"] == "build-2026.11.10"

    assert client.get(base + "/close-blockers", headers=h).json() == []

    closed = client.patch(base, headers=h, json={"status": "closed", "status_comment": "复测通过"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    assert closed.json()["closed_at"] is not None

    history = client.get(base + "/history", headers=h).json()
    transitions = [(row["from_status"], row["to_status"]) for row in history]
    assert ("open", "reopened") in transitions
    assert ("reopened", "resolved") in transitions
    assert ("resolved", "closed") in transitions
    assert history[-1]["comment"] == "复测通过"

    # Manual reopen invalidates the previous passed retest.
    reopened = client.patch(base, headers=h, json={"status": "reopened", "status_comment": "线上复现"})
    assert reopened.status_code == 200
    assert reopened.json()["verification_result"] is None
    blockers = client.get(base + "/close-blockers", headers=h).json()
    assert any("复测" in b for b in blockers), blockers


@patch("app.api.functional.testcases.enqueue_reindex")
def test_regression_cycle_must_be_green_before_close(_mock):
    _user("b5_regress")
    tok = _login("b5_regress")
    pid = _project(tok, "B5C")
    client = TestClient(app)
    h = _auth(tok)

    _, item_id, _ = _failed_item(client, pid, h)
    defect = client.post(f"/api/v1/projects/{pid}/defects/from-cycle-item/{item_id}", headers=h).json()
    base = f"/api/v1/projects/{pid}/defects/{defect['id']}"
    client.post(
        base + "/verify",
        headers=h,
        json={"result": "passed", "fix_build": "build-2026.11.10"},
    )

    # A regression batch with an unexecuted item blocks closing.
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "关联回归", "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "修复回归"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "修复批次", "status": "active"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"case_ids": [case["id"]]},
    ).json()

    linked = client.post(base + "/regression-cycles", headers=h, json={"cycle_ids": [cycle["id"]]})
    assert linked.status_code == 200, linked.text
    assert linked.json()["regression_cycle_ids"] == [cycle["id"]]

    blockers = client.get(base + "/close-blockers", headers=h).json()
    assert any("修复批次" in b for b in blockers), blockers
    blocked = client.patch(base, headers=h, json={"status": "closed"})
    assert blocked.status_code == 400

    # Passing the regression item unlocks closing.
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{items[0]['id']}",
        headers=h,
        json={"result": "passed", "actual_result": "ok"},
    )
    assert client.get(base + "/close-blockers", headers=h).json() == []
    assert client.patch(base, headers=h, json={"status": "closed"}).status_code == 200

    # Another project's batch cannot be linked.
    other = _project(tok, "B5D")
    other_plan = client.post(f"/api/v1/projects/{other}/test-plans/", headers=h, json={"name": "别的"}).json()
    other_cycle = client.post(
        f"/api/v1/projects/{other}/test-plans/{other_plan['id']}/cycles/",
        headers=h,
        json={"name": "别的批次"},
    ).json()
    bad = client.post(base + "/regression-cycles", headers=h, json={"cycle_ids": [other_cycle["id"]]})
    assert bad.status_code == 400


def test_failure_classification_rules():
    assert classify_failure("连接超时，服务不可达")[0] == "environment"
    assert classify_failure("测试账号不存在，前置数据缺失")[0] == "data"
    assert classify_failure("未找到元素 #submit，定位失败")[0] == "script"
    assert classify_failure("业务规则待确认：是否允许负数")[0] == "rule_pending"
    assert classify_failure("按钮文案不清晰，体验较差")[0] == "ux"
    assert classify_failure("金额计算错误")[0] == "product"
    assert classify_failure("")[0] == "product"
