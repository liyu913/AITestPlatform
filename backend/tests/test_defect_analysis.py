"""R4: defect analysis, conversion and submission attempts."""

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
        "/api/v1/projects/", headers={"Authorization": f"Bearer {token}"}, json={"name": name}
    )
    assert r.status_code == 201
    return r.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _failed_item(client: TestClient, pid: int, h: dict) -> tuple[int, int]:
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "提交订单无响应",
            "overall_expected": "订单创建成功",
            "steps": [{"order": 1, "action": "点击提交", "expected": "订单创建"}],
        },
    )
    assert case.status_code == 201, case.text
    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "计划"}
    )
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan.json()['id']}/cycles/",
        headers=h,
        json={"name": "批次", "target_build": "build-1", "env_kind": "ui"},
    )
    assert cycle.status_code == 201, cycle.text
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle.json()['id']}/items",
        headers=h,
        json={"assets": [{"asset_type": "functional", "asset_id": case.json()["id"]}]},
    )
    assert items.status_code == 201, items.text
    item = items.json()[0]
    patched = client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle.json()['id']}/items/{item['id']}",
        headers=h,
        json={"result": "failed", "actual_result": "点击后无响应"},
    )
    assert patched.status_code == 200, patched.text
    return item["id"], item["case_id"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_analysis_confirmation_conversion_and_internal_submission(_mock):
    _user("r4_analysis")
    tok = _login("r4_analysis")
    pid = _project(tok, "R4-ANALYSIS")
    client = TestClient(app)
    h = _auth(tok)
    item_id, case_id = _failed_item(client, pid, h)

    created = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/",
        headers=h,
        json={"cycle_item_id": item_id, "category": "product", "conclusion": "前端未处理空响应"},
    )
    assert created.status_code == 201, created.text
    analysis = created.json()
    assert analysis["source_type"] == "cycle_item"
    assert analysis["source_id"] == item_id
    assert analysis["observed"] == "点击后无响应"
    assert analysis["expected"] == "订单创建成功"
    assert analysis["evidence_json"]["cycle_item_id"] == item_id

    # Confirming requires a conclusion and a non-pending category.
    pending = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/", headers=h, json={"title": "待查"}
    )
    assert pending.status_code == 201
    bad = client.patch(
        f"/api/v1/projects/{pid}/defect-analyses/{pending.json()['id']}",
        headers=h,
        json={"status": "confirmed", "conclusion": "x"},
    )
    assert bad.status_code == 400, bad.text

    confirmed = client.patch(
        f"/api/v1/projects/{pid}/defect-analyses/{analysis['id']}",
        headers=h,
        json={"status": "confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    converted = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/{analysis['id']}/convert", headers=h
    )
    assert converted.status_code == 200, converted.text
    defect = converted.json()
    assert defect["cycle_item_id"] == item_id
    assert defect["case_id"] == case_id
    assert defect["category"] == "product"
    assert defect["found_build"] == "build-1"

    again = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/{analysis['id']}/convert", headers=h
    )
    assert again.status_code == 409, again.text

    pending_defects = client.get(
        f"/api/v1/projects/{pid}/defects/pending-submission", headers=h
    ).json()
    assert [row["id"] for row in pending_defects] == [defect["id"]]

    first = client.post(
        f"/api/v1/projects/{pid}/defects/{defect['id']}/submissions",
        headers=h,
        json={"target": "internal", "request_id": "req-1"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "success"
    assert first.json()["external_id"] == f"LY-{defect['id']}"

    repeat = client.post(
        f"/api/v1/projects/{pid}/defects/{defect['id']}/submissions",
        headers=h,
        json={"target": "internal", "request_id": "req-1"},
    )
    assert repeat.status_code == 200
    assert repeat.json()["id"] == first.json()["id"]

    assert client.get(f"/api/v1/projects/{pid}/defects/pending-submission", headers=h).json() == []
    history = client.get(
        f"/api/v1/projects/{pid}/defects/{defect['id']}/submissions", headers=h
    ).json()
    assert len(history) == 1


@patch("app.api.functional.testcases.enqueue_reindex")
def test_non_product_analysis_can_dismiss_and_external_submission_is_recorded(_mock):
    _user("r4_dismiss")
    tok = _login("r4_dismiss")
    pid = _project(tok, "R4-DISMISS")
    client = TestClient(app)
    h = _auth(tok)
    item_id, _case_id = _failed_item(client, pid, h)

    env_analysis = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/",
        headers=h,
        json={
            "cycle_item_id": item_id,
            "category": "environment",
            "conclusion": "测试环境数据库连接超时，非产品问题",
        },
    ).json()
    dismissed = client.patch(
        f"/api/v1/projects/{pid}/defect-analyses/{env_analysis['id']}",
        headers=h,
        json={"status": "dismissed"},
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"
    bad_convert = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/{env_analysis['id']}/convert", headers=h
    )
    assert bad_convert.status_code == 400

    # Manual product analysis can still produce a defect and submit externally.
    manual = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/",
        headers=h,
        json={
            "title": "登录按钮错位",
            "observed": "按钮覆盖输入框",
            "expected": "按钮在下方",
            "category": "product",
            "conclusion": "样式回归",
        },
    ).json()
    assert manual["status"] == "confirmed"
    defect = client.post(
        f"/api/v1/projects/{pid}/defect-analyses/{manual['id']}/convert", headers=h
    ).json()

    failed = client.post(
        f"/api/v1/projects/{pid}/defects/{defect['id']}/submissions",
        headers=h,
        json={"target": "external", "request_id": "ext-1"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert failed.json()["attempt_no"] == 1

    retry = client.post(
        f"/api/v1/projects/{pid}/defects/{defect['id']}/submissions",
        headers=h,
        json={"target": "external", "request_id": "ext-2", "connector": "jira"},
    )
    assert retry.status_code == 200
    assert retry.json()["attempt_no"] == 2
