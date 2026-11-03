import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from tests.conftest import TestingSessionLocal


def _user(username: str, pw: str):
    db = TestingSessionLocal()
    try:
        db.add(User(username=username, password_hash=auth_service.hash_password(pw), is_active=True))
        db.commit()
    finally:
        db.close()


def _login(username: str, pw: str) -> str:
    r = TestClient(app).post("/api/v1/auth/login", json={"username": username, "password": pw})
    assert r.status_code == 200
    return r.json()["access_token"]


def _project(token: str) -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "TC"},
    )
    assert r.status_code == 201
    return r.json()["id"]


@patch("app.api.functional.testcases.enqueue_reindex")
def test_create_case_with_steps(mock_rq):
    _user("tc_adm", "a")
    tok = _login("tc_adm", "a")
    pid = _project(tok)
    client = TestClient(app)

    r = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "C1",
            "steps": [
                {"order": 0, "action": "click", "expected": "ok"},
                {"order": 1, "action": "save", "expected": "saved"},
            ],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert len(body["steps"]) == 2
    assert body.get("overall_expected") == ""
    mock_rq.assert_called_with(pid, "case", body["id"])


def test_overall_expected_create_get_patch():
    _user("tc_ov", "o")
    tok = _login("tc_ov", "o")
    pid = _project(tok)
    client = TestClient(app)
    cr = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "With overall",
            "overall_expected": "系统登录成功",
            "steps": [{"action": "a", "expected": "e"}],
        },
    )
    assert cr.status_code == 201
    cid = cr.json()["id"]
    assert cr.json()["overall_expected"] == "系统登录成功"

    r_get = client.get(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_get.status_code == 200
    assert r_get.json()["overall_expected"] == "系统登录成功"

    r_patch = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"overall_expected": "会话有效"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["overall_expected"] == "会话有效"


def test_test_data_create_get_patch():
    _user("tc_td", "t")
    tok = _login("tc_td", "t")
    pid = _project(tok)
    client = TestClient(app)
    cr = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "搜索房源",
            "test_data": "  关键词 三室两厅  ",
            "overall_expected": "列表展示匹配房源",
            "steps": [{"action": "输入关键词并搜索", "expected": "—"}],
        },
    )
    assert cr.status_code == 201
    cid = cr.json()["id"]
    assert cr.json()["test_data"] == "关键词 三室两厅"

    r_get = client.get(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_get.status_code == 200
    assert r_get.json()["test_data"] == "关键词 三室两厅"

    r_keep = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "搜索房源-改标题"},
    )
    assert r_keep.status_code == 200
    assert r_keep.json()["test_data"] == "关键词 三室两厅"

    r_patch = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"test_data": "  "},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["test_data"] is None


@patch("app.api.functional.testcases.enqueue_reindex")
def test_variables_create_get_patch(mock_rq):
    _user("tc_vars", "v")
    tok = _login("tc_vars", "v")
    pid = _project(tok)
    client = TestClient(app)
    cr = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "登录",
            "variables": {"username": "testuser", "password": "Test123456"},
            "overall_expected": "登录成功",
            "steps": [{"action": "输入账号密码", "expected": "—"}],
        },
    )
    assert cr.status_code == 201
    body = cr.json()
    assert body["variables"] == {"username": "testuser", "password": "Test123456"}
    assert "username：testuser" in (body.get("test_data") or "")
    cid = body["id"]

    r_get = client.get(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_get.status_code == 200
    assert r_get.json()["variables"]["password"] == "Test123456"

    r_patch = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"variables": {"username": "admin"}},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["variables"] == {"username": "admin"}
    assert "password" not in r_patch.json()["variables"]

    # Free-form KV prose still parses into variables when variables omitted
    cr2 = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "从文本解析",
            "test_data": "username：alice；password：secret",
            "steps": [{"action": "a", "expected": "e"}],
        },
    )
    assert cr2.status_code == 201
    assert cr2.json()["variables"] == {"username": "alice", "password": "secret"}


def test_precondition_and_case_type_create_get_patch():
    _user("tc_pt", "p")
    tok = _login("tc_pt", "p")
    pid = _project(tok)
    client = TestClient(app)
    cr = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "登录失败",
            "precondition": "  账号已注册  ",
            "case_type": "异常",
            "overall_expected": "提示密码错误",
            "steps": [{"action": "输入错误密码", "expected": "—"}],
        },
    )
    assert cr.status_code == 201
    cid = cr.json()["id"]
    assert cr.json()["precondition"] == "账号已注册"
    assert cr.json()["case_type"] == "异常"

    r_get = client.get(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r_get.status_code == 200
    assert r_get.json()["precondition"] == "账号已注册"
    assert r_get.json()["case_type"] == "异常"

    r_keep = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "登录失败-改标题"},
    )
    assert r_keep.status_code == 200
    assert r_keep.json()["case_type"] == "异常"
    assert r_keep.json()["precondition"] == "账号已注册"

    r_patch = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"precondition": "  ", "case_type": "性能"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["precondition"] is None
    assert r_patch.json()["case_type"] is None


def test_patch_replaces_steps():
    _user("tc_own", "o")
    tok = _login("tc_own", "o")
    pid = _project(tok)
    client = TestClient(app)
    cr = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={
            "title": "C",
            "steps": [{"action": "a1", "expected": "e1"}],
        },
    )
    cid = cr.json()["id"]

    r2 = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{cid}",
        headers={"Authorization": f"Bearer {tok}"},
        json={"steps": [{"order": 0, "action": "new", "expected": "n"}]},
    )
    assert r2.status_code == 200
    assert len(r2.json()["steps"]) == 1
    assert r2.json()["steps"][0]["action"] == "new"


def test_viewer_cannot_create_case():
    _user("tc_boss", "b")
    _user("tc_view", "v")
    btok = _login("tc_boss", "b")
    pid = _project(btok)
    client = TestClient(app)
    client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {btok}"},
        json={"username": "tc_view", "role": "viewer"},
    )
    vtok = _login("tc_view", "v")
    r = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {vtok}"},
        json={"title": "X", "steps": []},
    )
    assert r.status_code == 403


def test_only_admin_deletes_case():
    _user("tc_a", "a")
    _user("tc_e", "e")
    atok = _login("tc_a", "a")
    pid = _project(atok)
    client = TestClient(app)
    client.post(
        f"/api/v1/projects/{pid}/members",
        headers={"Authorization": f"Bearer {atok}"},
        json={"username": "tc_e", "role": "editor"},
    )
    etok = _login("tc_e", "e")
    cid = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {etok}"},
        json={"title": "D", "steps": []},
    ).json()["id"]

    assert (
        client.delete(
            f"/api/v1/projects/{pid}/test-cases/{cid}",
            headers={"Authorization": f"Bearer {etok}"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/projects/{pid}/test-cases/{cid}",
            headers={"Authorization": f"Bearer {atok}"},
        ).status_code
        == 204
    )


@patch("app.api.functional.testcases.enqueue_reindex")
def test_list_cases_pagination_and_search(_mock_rq):
    _user("tc_pg", "p")
    tok = _login("tc_pg", "p")
    pid = _project(tok)
    client = TestClient(app)
    for title in ["Case-A", "Case-AB", "Other"]:
        client.post(
            f"/api/v1/projects/{pid}/test-cases/",
            headers={"Authorization": f"Bearer {tok}"},
            json={"title": title, "steps": []},
        )
    r1 = client.get(
        f"/api/v1/projects/{pid}/test-cases/?page=1&page_size=2",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 3
    assert len(d1["items"]) == 2

    r2 = client.get(
        f"/api/v1/projects/{pid}/test-cases/?search=Case-A",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r2.status_code == 200
    d2 = r2.json()
    titles = [x["title"] for x in d2["items"]]
    assert "Case-A" in titles
    assert "Case-AB" in titles
    assert "Other" not in titles


def test_list_cases_filter_by_requirement_id():
    _user("tc_rf", "x")
    tok = _login("tc_rf", "x")
    pid = _project(tok)
    client = TestClient(app)
    rid = client.post(
        f"/api/v1/projects/{pid}/requirements/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "Req1", "body_md": "body"},
    ).json()["id"]
    client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "Linked", "requirement_id": rid, "steps": [{"action": "a", "expected": "e"}]},
    )
    client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers={"Authorization": f"Bearer {tok}"},
        json={"title": "Unlinked", "steps": [{"action": "a", "expected": "e"}]},
    )
    r = client.get(
        f"/api/v1/projects/{pid}/test-cases/?requirement_id={rid}",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Linked"
    assert data["items"][0]["requirement_id"] == rid


@patch("app.api.functional.testcases.enqueue_reindex")
def test_list_cases_newest_updated_first(_mock_rq):
    _user("tc_ord", "o")
    tok = _login("tc_ord", "o")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}
    a = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "Case-A", "steps": []},
    )
    b = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "Case-B", "steps": []},
    )
    assert a.status_code == 201
    assert b.status_code == 201
    time.sleep(1.1)
    patched = client.patch(
        f"/api/v1/projects/{pid}/test-cases/{a.json()['id']}",
        headers=h,
        json={"title": "Case-A-updated"},
    )
    assert patched.status_code == 200
    listed = client.get(f"/api/v1/projects/{pid}/test-cases/", headers=h)
    assert listed.status_code == 200
    titles = [x["title"] for x in listed.json()["items"]]
    assert titles[0] == "Case-A-updated"
    assert titles[1] == "Case-B"


@patch("app.api.functional.testcases.enqueue_reindex")
def test_linked_ui_case_count_and_filter(mock_rq):
    _user("tc_link", "a")
    tok = _login("tc_link", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    tc = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={"title": "Linked", "overall_expected": "ok", "steps": []},
    )
    assert tc.status_code == 201, tc.text
    case_id = tc.json()["id"]
    assert tc.json().get("linked_ui_case_count", 0) == 0

    listed0 = client.get(f"/api/v1/projects/{pid}/test-cases/", headers=h)
    assert listed0.status_code == 200
    row0 = next(x for x in listed0.json()["items"] if x["id"] == case_id)
    assert row0["linked_ui_case_count"] == 0

    uc = client.post(
        f"/api/v1/projects/{pid}/ui-cases/",
        headers=h,
        json={
            "name": "UI from Linked",
            "script": "def run(page):\n    page.goto('/')\n",
            "status": "ready",
            "manual_case_id": case_id,
        },
    )
    assert uc.status_code == 201, uc.text

    listed1 = client.get(f"/api/v1/projects/{pid}/test-cases/", headers=h)
    assert listed1.status_code == 200
    row1 = next(x for x in listed1.json()["items"] if x["id"] == case_id)
    assert row1["linked_ui_case_count"] == 1

    filtered = client.get(
        f"/api/v1/projects/{pid}/ui-cases/?manual_case_id={case_id}",
        headers=h,
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["manual_case_id"] == case_id

    empty = client.get(
        f"/api/v1/projects/{pid}/ui-cases/?manual_case_id={case_id + 99999}",
        headers=h,
    )
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


@patch("app.api.functional.testcases.enqueue_reindex")
def test_list_filters_module_priority_status(mock_rq):
    _user("tc_filt", "a")
    tok = _login("tc_filt", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    a = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "Login happy",
            "module": "登录",
            "priority": "P0",
            "status": "ready",
            "steps": [{"action": "login", "expected": "ok"}],
        },
    )
    b = client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "Search edge",
            "module": "搜索",
            "priority": "P2",
            "status": "draft",
            "steps": [{"action": "search", "expected": "—"}],
        },
    )
    assert a.status_code == 201
    assert b.status_code == 201

    by_mod = client.get(
        f"/api/v1/projects/{pid}/test-cases/?module=%E7%99%BB%E5%BD%95",
        headers=h,
    )
    assert by_mod.status_code == 200
    assert by_mod.json()["total"] == 1
    assert by_mod.json()["items"][0]["title"] == "Login happy"

    by_pri = client.get(f"/api/v1/projects/{pid}/test-cases/?priority=P2", headers=h)
    assert by_pri.status_code == 200
    assert by_pri.json()["total"] == 1
    assert by_pri.json()["items"][0]["title"] == "Search edge"

    by_st = client.get(f"/api/v1/projects/{pid}/test-cases/?status=ready", headers=h)
    assert by_st.status_code == 200
    assert by_st.json()["total"] == 1
    assert by_st.json()["items"][0]["id"] == a.json()["id"]

    modules = client.get(f"/api/v1/projects/{pid}/test-cases/modules", headers=h)
    assert modules.status_code == 200
    assert modules.json() == ["搜索", "登录"] or set(modules.json()) == {"搜索", "登录"}


@patch("app.api.functional.testcases.enqueue_reindex")
def test_export_xlsx_xmind_respects_filters(mock_rq):
    import io
    import json
    import zipfile

    from openpyxl import load_workbook

    _user("tc_exp", "a")
    tok = _login("tc_exp", "a")
    pid = _project(tok)
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "Keep Me",
            "module": "A",
            "priority": "P0",
            "status": "ready",
            "overall_expected": "pass",
            "steps": [{"action": "do", "expected": "ok"}],
        },
    )
    client.post(
        f"/api/v1/projects/{pid}/test-cases/",
        headers=h,
        json={
            "title": "Skip Me",
            "module": "B",
            "priority": "P3",
            "status": "draft",
            "steps": [{"action": "x", "expected": "—"}],
        },
    )

    xlsx = client.get(
        f"/api/v1/projects/{pid}/test-cases/export?format=xlsx&module=A&status=ready",
        headers=h,
    )
    assert xlsx.status_code == 200
    assert "attachment" in xlsx.headers.get("content-disposition", "")
    assert xlsx.headers.get("content-disposition", "").endswith('.xlsx"')
    wb = load_workbook(io.BytesIO(xlsx.content))
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0][0] == "ID"
    data_rows = rows[1:]
    assert len(data_rows) == 1
    assert data_rows[0][1] == "Keep Me"

    xmind = client.get(
        f"/api/v1/projects/{pid}/test-cases/export?format=xmind&module=A",
        headers=h,
    )
    assert xmind.status_code == 200
    assert xmind.headers.get("content-disposition", "").endswith('.xmind"')
    with zipfile.ZipFile(io.BytesIO(xmind.content)) as zf:
        content = json.loads(zf.read("content.json").decode("utf-8"))
    titles = []

    def walk(node):
        if isinstance(node, dict):
            t = node.get("title")
            if t:
                titles.append(t)
            kids = (node.get("children") or {}).get("attached") or []
            for k in kids:
                walk(k)

    walk(content[0]["rootTopic"])
    assert "Keep Me" in titles
    assert "Skip Me" not in titles

    empty = client.get(
        f"/api/v1/projects/{pid}/test-cases/export?format=xlsx&module=NoSuch",
        headers=h,
    )
    assert empty.status_code == 200
    wb_empty = load_workbook(io.BytesIO(empty.content))
    assert len(list(wb_empty.active.iter_rows(values_only=True))) == 1
