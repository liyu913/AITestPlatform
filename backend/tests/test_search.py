"""B1/B7: project-wide jump search and its deep links."""

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


@patch("app.api.functional.testcases.enqueue_reindex")
def test_search_finds_every_asset_kind_with_links(_mock):
    _user("search_owner")
    tok = _login("search_owner")
    pid = _project(tok, "搜索项目")
    client = TestClient(app)
    h = _auth(tok)

    requirement = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers=h,
        json={"title": "订单需求", "body_md": "订单流程说明", "status": "active"},
    ).json()
    case = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "订单校验", "requirement_id": requirement["id"],
              "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    ).json()
    plan = client.post(f"/api/v1/projects/{pid}/test-plans/", headers=h, json={"name": "订单计划"}).json()
    cycle = client.post(
        f"/api/v1/projects/{pid}/test-plans/{plan['id']}/cycles/", headers=h, json={"name": "订单批次"}
    ).json()
    defect = client.post(
        f"/api/v1/projects/{pid}/defects/", headers=h, json={"title": "订单缺陷", "severity": "P2"}
    ).json()
    api_case = client.post(
        f"/api/v1/projects/{pid}/api-cases/", headers=h, json={"name": "订单接口", "path": "/orders"}
    )
    assert api_case.status_code == 201, api_case.text
    ui_case = client.post(
        f"/api/v1/projects/{pid}/ui-cases/", headers=h, json={"name": "订单界面"}
    )
    assert ui_case.status_code == 201, ui_case.text

    found = client.get(f"/api/v1/projects/{pid}/search", headers=h, params={"q": "订单"})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["query"] == "订单"
    by_kind = {group["kind"]: group for group in body["groups"]}
    assert {"requirement", "test_case", "test_plan", "defect", "api_case", "ui_case"} <= set(by_kind)
    assert body["total"] == sum(len(group["items"]) for group in body["groups"])

    requirement_hit = by_kind["requirement"]["items"][0]
    assert requirement_hit["id"] == requirement["id"]
    assert requirement_hit["link"] == f"requirements?requirement={requirement['id']}"

    case_hit = by_kind["test_case"]["items"][0]
    assert case_hit["title"] == "订单校验"
    assert case_hit["link"] == f"test-cases?case={case['id']}"
    assert case_hit["subtitle"].startswith("v1")

    plan_links = {item["link"] for item in by_kind["test_plan"]["items"]}
    assert f"test-plans/{plan['id']}" in plan_links
    assert f"cycles/{cycle['id']}" in plan_links

    defect_hit = by_kind["defect"]["items"][0]
    assert defect_hit["link"] == f"defects?defect={defect['id']}"
    api_hit = by_kind["api_case"]["items"][0]
    assert api_hit["link"] == f"api-cases?caseId={api_case.json()['id']}"
    ui_hit = by_kind["ui_case"]["items"][0]
    assert ui_hit["link"] == f"ui-cases?case={ui_case.json()['id']}"

    # Requirement body matches even when the title does not.
    body_hit = client.get(
        f"/api/v1/projects/{pid}/search", headers=h, params={"q": "流程说明"}
    ).json()
    assert [g["kind"] for g in body_hit["groups"]] == ["requirement"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_search_escapes_wildcards_and_scopes_to_project(_mock):
    _user("search_a")
    _user("search_b")
    tok_a = _login("search_a")
    tok_b = _login("search_b")
    pid_a = _project(tok_a, "搜索 A")
    pid_b = _project(tok_b, "搜索 B")
    client = TestClient(app)
    h_a = _auth(tok_a)

    client.post(
        f"/api/v1/projects/{pid_a}/test-cases/",
        headers=h_a,
        json={"title": "进度 100% 完成", "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    )
    client.post(
        f"/api/v1/projects/{pid_a}/test-cases/",
        headers=h_a,
        json={"title": "普通用例", "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    )
    client.post(
        f"/api/v1/projects/{pid_b}/test-cases/",
        headers=_auth(tok_b),
        json={"title": "进度 100% 完成（另一个项目）", "steps": [{"order": 1, "action": "a", "expected": "e"}]},
    )

    percent = client.get(f"/api/v1/projects/{pid_a}/search", headers=h_a, params={"q": "100%"}).json()
    hits = [item for group in percent["groups"] for item in group["items"]]
    assert [hit["title"] for hit in hits] == ["进度 100% 完成"]

    empty = client.get(f"/api/v1/projects/{pid_a}/search", headers=h_a, params={"q": ""}).json()
    assert empty["groups"] == [] and empty["total"] == 0

    # Another project's records never leak through search.
    other = client.get(f"/api/v1/projects/{pid_b}/search", headers=h_a)
    assert other.status_code in (403, 404)


def test_search_requires_membership():
    _user("search_owner2")
    _user("search_outsider2")
    owner = _login("search_owner2")
    outsider = _login("search_outsider2")
    pid = _project(owner, "搜索隔离")
    denied = TestClient(app).get(
        f"/api/v1/projects/{pid}/search", headers=_auth(outsider), params={"q": "x"}
    )
    assert denied.status_code in (403, 404)
