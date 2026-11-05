"""B6: batch report snapshots, statistics rules, retries and export."""

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


def _setup(client: TestClient, pid: int, h: dict) -> dict:
    req_case = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "下单需求", "body_md": "下单", "acceptance_rules": "下单成功返回订单号"},
    ).json()
    req_rule = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "规则需求", "body_md": "规则", "acceptance_rules": "仅允许正数金额"},
    ).json()
    req_none = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "无覆盖需求", "body_md": "无"},
    ).json()

    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "下单用例", "requirement_id": req_case["id"],
              "steps": [{"order": 1, "action": "提交", "expected": "创建成功"}]},
    ).json()
    plan = client.post(
        f"/api/v1/projects/{pid}/test-plans/",
        headers=h,
        json={"name": "发布计划", "version": "1.0.0"},
    ).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/",
        headers=h,
        json={"name": "发布批次", "status": "active", "target_build": "build-9"},
    ).json()
    return {
        "req_case": req_case,
        "req_rule": req_rule,
        "req_none": req_none,
        "case": case,
        "plan": plan,
        "cycle": cycle,
    }


def _add_items(
    client: TestClient, pid: int, h: dict, cycle_id: int, count: int, *, requirement_id: int | None = None
) -> list[int]:
    case_ids = []
    for i in range(count):
        payload: dict = {
            "title": f"用例 {i}",
            "steps": [{"order": 1, "action": "a", "expected": "e"}],
        }
        if requirement_id is not None:
            payload["requirement_id"] = requirement_id
        case = client.post(f"/api/v1/projects/{pid}/test-cases/", headers=h, json=payload).json()
        case_ids.append(case["id"])
    items = client.post(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items", headers=h, json={"case_ids": case_ids}
    ).json()
    return [item["id"] for item in items]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_empty_batch_report_reports_not_applicable(_mock):
    _user("b6_empty")
    tok = _login("b6_empty")
    pid = _project(tok, "B6EMPTY")
    client = TestClient(app)
    h = _auth(tok)
    ctx = _setup(client, pid, h)

    created = client.post(
        f"/api/v1/projects/{pid}/reports/cycles/{ctx['cycle']['id']}", headers=h
    )
    assert created.status_code == 201, created.text
    report = created.json()
    snapshot = report["snapshot"]
    assert snapshot["metrics"]["selected_total"] == 0
    assert snapshot["metrics"]["pass_rate"] is None
    assert snapshot["metrics"]["completion_rate"] is None
    assert any(r["code"] == "empty_scope" for r in snapshot["risks"])
    assert "不适用" in report["summary_text"]

    md = client.get(f"/api/v1/projects/{pid}/reports/{report['id']}/export?format=md", headers=h)
    assert md.status_code == 200
    assert "不适用" in md.text


@patch("app.api.functional.testcases.enqueue_reindex")
def test_mixed_results_metrics_and_coverage_kinds(_mock):
    _user("b6_mixed")
    tok = _login("b6_mixed")
    pid = _project(tok, "B6MIX")
    client = TestClient(app)
    h = _auth(tok)
    ctx = _setup(client, pid, h)
    cycle_id = ctx["cycle"]["id"]
    item_ids = _add_items(client, pid, h, cycle_id, 4)
    base = f"/api/v1/projects/{pid}/cycles/{cycle_id}/items"
    client.patch(f"{base}/{item_ids[0]}", headers=h, json={"result": "passed"})
    client.patch(f"{base}/{item_ids[1]}", headers=h, json={"result": "failed", "actual_result": "无响应"})
    client.patch(f"{base}/{item_ids[2]}", headers=h, json={"result": "blocked"})
    # item 3 stays untested

    report = client.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle_id}", headers=h).json()
    metrics = report["snapshot"]["metrics"]
    assert metrics["selected_total"] == 4
    assert metrics["passed"] == 1 and metrics["failed"] == 1
    assert metrics["blocked"] == 1 and metrics["untested"] == 1
    assert metrics["pass_rate"] == 0.5           # 1 / (1+1)
    assert metrics["completion_rate"] == 0.5     # (1+1) / 4

    codes = {risk["code"] for risk in report["snapshot"]["risks"]}
    assert {"failed", "blocked", "untested"} <= codes

    kinds = {
        row["requirement_id"]: row["coverage_kind"] for row in report["snapshot"]["requirements"]
    }
    assert kinds[ctx["req_case"]["id"]] == "case"
    assert kinds[ctx["req_rule"]["id"]] == "rule"
    assert kinds[ctx["req_none"]["id"]] == "none"


@patch("app.api.functional.testcases.enqueue_reindex")
def test_retry_running_and_skipped_are_separate_buckets(_mock):
    _user("b6_retry")
    tok = _login("b6_retry")
    pid = _project(tok, "B6RETRY")
    client = TestClient(app)
    h = _auth(tok)
    ctx = _setup(client, pid, h)
    cycle_id = ctx["cycle"]["id"]
    item_ids = _add_items(client, pid, h, cycle_id, 3)
    att = f"/api/v1/projects/{pid}/cycles/{cycle_id}/items"

    # A: failed then retried to pass — first failure must stay visible.
    client.post(
        f"{att}/{item_ids[0]}/attempts",
        headers=h,
        json={"execution_type": "agent_once", "status": "failed", "summary": "第一次超时"},
    )
    client.post(
        f"{att}/{item_ids[0]}/attempts",
        headers=h,
        json={"execution_type": "agent_once", "status": "passed", "summary": "重试通过"},
    )
    # B: still running.
    client.post(
        f"{att}/{item_ids[1]}/attempts",
        headers=h,
        json={"execution_type": "ui", "status": "running"},
    )
    # C: cancelled -> skipped, not untested.
    client.post(
        f"{att}/{item_ids[2]}/attempts",
        headers=h,
        json={"execution_type": "ui", "status": "cancelled", "summary": "手动停止"},
    )

    report = client.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle_id}", headers=h).json()
    metrics = report["snapshot"]["metrics"]
    assert metrics["passed"] == 1
    assert metrics["running"] == 1
    assert metrics["skipped"] == 1
    assert metrics["untested"] == 0  # every item is in exactly one bucket
    assert metrics["selected_total"] == 3
    assert metrics["pass_rate"] == 1.0
    assert metrics["completion_rate"] == round(1 / 3, 4)

    retried = report["snapshot"]["attempts"]["retried_to_pass"]
    assert len(retried) == 1
    assert retried[0]["item_id"] == item_ids[0]
    assert retried[0]["first_failed_attempt"] == 1
    assert "超时" in retried[0]["first_failure"]
    codes = {risk["code"] for risk in report["snapshot"]["risks"]}
    assert "flaky_retry" in codes and "running" in codes and "skipped" in codes

    xlsx = client.get(f"/api/v1/projects/{pid}/reports/{report['id']}/export?format=xlsx", headers=h)
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"


@patch("app.api.functional.testcases.enqueue_reindex")
def test_report_revisions_are_immutable(_mock):
    _user("b6_rev")
    tok = _login("b6_rev")
    pid = _project(tok, "B6REV")
    client = TestClient(app)
    h = _auth(tok)
    ctx = _setup(client, pid, h)
    cycle_id = ctx["cycle"]["id"]
    item_ids = _add_items(
        client, pid, h, cycle_id, 1, requirement_id=ctx["req_case"]["id"]
    )
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item_ids[0]}",
        headers=h,
        json={"result": "failed"},
    )

    first = client.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle_id}", headers=h).json()
    assert first["revision"] == 1
    assert first["snapshot"]["metrics"]["failed"] == 1

    # Fix the result, then regenerate: revision 2 reflects it, revision 1 does not.
    client.patch(
        f"/api/v1/projects/{pid}/cycles/{cycle_id}/items/{item_ids[0]}",
        headers=h,
        json={"result": "passed"},
    )
    second = client.post(f"/api/v1/projects/{pid}/reports/cycles/{cycle_id}", headers=h).json()
    assert second["revision"] == 2
    assert second["snapshot"]["metrics"]["passed"] == 1

    history = client.get(f"/api/v1/projects/{pid}/reports/cycles/{cycle_id}", headers=h).json()
    assert [row["revision"] for row in history] == [2, 1]

    reloaded = client.get(f"/api/v1/projects/{pid}/reports/{first['id']}", headers=h).json()
    assert reloaded["snapshot"]["metrics"]["failed"] == 1  # unchanged snapshot
    assert reloaded["snapshot"]["metrics"]["passed"] == 0

    project_reports = client.get(f"/api/v1/projects/{pid}/reports", headers=h).json()
    assert {row["id"] for row in project_reports} == {first["id"], second["id"]}
    assert project_reports[0]["cycle_name"] == "发布批次"

    # Comparing revisions shows what improved between them.
    compared = client.get(
        f"/api/v1/projects/{pid}/reports/{second['id']}/compare", headers=h
    )
    assert compared.status_code == 200, compared.text
    diff = compared.json()
    assert diff["current_revision"] == 2 and diff["previous_revision"] == 1
    metrics = {row["key"]: row for row in diff["metrics"]}
    assert metrics["passed"]["delta"] == 1
    assert metrics["failed"]["delta"] == -1
    assert metrics["pass_rate"]["delta"] == 1.0
    assert any("失败" in message for message in diff["risks_resolved"])
    assert diff["requirements_changed"], diff

    explicit = client.get(
        f"/api/v1/projects/{pid}/reports/{second['id']}/compare",
        headers=h,
        params={"against": first["id"]},
    )
    assert explicit.status_code == 200
    assert explicit.json()["previous_id"] == first["id"]

    self_compare = client.get(
        f"/api/v1/projects/{pid}/reports/{second['id']}/compare",
        headers=h,
        params={"against": second["id"]},
    )
    assert self_compare.status_code == 400
