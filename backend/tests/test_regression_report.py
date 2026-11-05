"""R5: regression task creation and report narrative snapshot."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
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


def _profile(name: str, model: str) -> None:
    db = TestingSessionLocal()
    try:
        db.add(SystemLlmProfile(name=name, api_base="https://llm.example.com/v1", model=model, is_active=True))
        db.commit()
    finally:
        db.close()


def _defect_with_case(client: TestClient, pid: int, h: dict) -> dict:
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "支付回调失败",
            "overall_expected": "订单状态更新",
            "steps": [{"order": 1, "action": "回调", "expected": "状态更新"}],
        },
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "失败批次", "target_build": "build-1", "env_kind": "api"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "functional", "asset_id": case["id"]}]},
    ).json()
    item = items[0]
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{item['id']}",
        headers=h,
        json={"result": "failed", "actual_result": "回调后状态未更新"},
    )
    defect = client.post(
        f"/api/v1/projects/{pid}/defects/from-cycle-item/{item['id']}", headers=h
    )
    assert defect.status_code == 201, defect.text
    return defect.json()


@patch("app.api.functional.testcases.enqueue_reindex")
def test_regression_task_creates_cycle_and_links_defects(_mock):
    _user("r5_reg")
    tok = _login("r5_reg")
    pid = _project(tok, "R5-REG")
    client = TestClient(app)
    h = _auth(tok)
    defect = _defect_with_case(client, pid, h)

    created = client.post(
        f"/api/v1/projects/{pid}/defects/regression-tasks",
        headers=h,
        json={
            "defect_ids": [defect["id"]],
            "cycle_name": "修复回归 1",
            "target_build": "build-fix-1",
            "env_kind": "api",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["item_count"] == 1
    assert payload["defect_ids"] == [defect["id"]]

    cycle = client.get(f"/api/v1/projects/{pid}/cycles/{payload['cycle_id']}", headers=h).json()
    assert cycle["target_build"] == "build-fix-1"
    assert cycle["item_count"] == 1

    items = client.get(
        f"/api/v1/projects/{pid}/cycles/{payload['cycle_id']}/items/", headers=h
    ).json()
    assert items["items"][0]["case_id"] == defect["case_id"]

    after = client.get(f"/api/v1/projects/{pid}/defects/{defect['id']}", headers=h).json()
    assert payload["cycle_id"] in (after.get("regression_cycle_ids") or [])

    missing = client.post(
        f"/api/v1/projects/{pid}/defects/regression-tasks",
        headers=h,
        json={"defect_ids": [99999], "cycle_name": "x"},
    )
    assert missing.status_code == 404


@patch("app.api.functional.reports.llm.chat_complete")
@patch("app.api.functional.testcases.enqueue_reindex")
def test_report_narrative_uses_resolved_model_and_keeps_config(_reindex, chat):
    _user("r5_report")
    tok = _login("r5_report")
    pid = _project(tok, "R5-REPORT")
    client = TestClient(app)
    h = _auth(tok)
    _profile("narrative-model", "model-narrative")
    chat.return_value = "本批次通过率 100%，遗留风险一项，建议继续观察。"

    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "登录成功",
            "overall_expected": "进入首页",
            "steps": [{"order": 1, "action": "登录", "expected": "首页"}],
        },
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "报告批次", "target_build": "build-2", "env_kind": "api"},
    ).json()
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "functional", "asset_id": case["id"]}]},
    ).json()
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle['id']}/items/{items[0]['id']}",
        headers=h,
        json={"result": "passed", "actual_result": "进入首页"},
    )
    report = client.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle['id']}", headers=h)
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]

    narrative = client.post(f"/api/v1/projects/{pid}/reports/{report_id}/narrative", headers=h)
    assert narrative.status_code == 200, narrative.text
    body = narrative.json()
    assert body["narrative"].startswith("本批次通过率")
    assert body["narrative_config"]["action"] == "regression_report"
    assert body["narrative_config"]["model_name"] == "model-narrative"
    assert body["narrative_config"]["source"] == "system"
    chat.assert_called_once()
