from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.perf.perf_runner import evaluate_sla
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


def _project(token: str, name: str = "Perf") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_evaluate_sla_pass_and_fail():
    metrics = {"p95_ms": 100.0, "fail_ratio": 0.01}
    ok = evaluate_sla(metrics, {"max_p95_ms": 200, "max_error_rate": 0.05})
    assert ok["passed"] is True
    assert all(c["ok"] for c in ok["checks"])

    bad = evaluate_sla(metrics, {"max_p95_ms": 50, "max_error_rate": 0.001})
    assert bad["passed"] is False
    assert any(not c["ok"] for c in bad["checks"])


def test_perf_scenario_crud_and_run(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("perf_a", "p")
    tok = _login("perf_a", "p")
    pid = _project(tok, "PerfP1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://api.example.com", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    cr = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "ping", "method": "GET", "path": "/ping", "status": "ready"},
    )
    assert cr.status_code == 201
    case_id = cr.json()["id"]

    # empty scenario rejected
    bad = client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={"name": "empty", "case_items": []},
    )
    assert bad.status_code == 400

    # users over limit
    over = client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={
            "name": "over",
            "case_items": [{"case_id": case_id, "weight": 1}],
            "users": 201,
        },
    )
    assert over.status_code == 422

    sr = client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={
            "name": "smoke",
            "env_id": env_id,
            "case_items": [{"case_id": case_id, "weight": 2}],
            "users": 5,
            "spawn_rate": 2,
            "run_time_sec": 3,
            "think_time_ms": 0,
            "sla": {"max_p95_ms": 500, "max_error_rate": 0.1},
        },
    )
    assert sr.status_code == 201, sr.text
    scenario_id = sr.json()["id"]
    assert sr.json()["case_items"][0]["weight"] == 2

    lst = client.get(f"/api/v1/projects/{pid}/perf-scenarios/", headers=h)
    assert lst.status_code == 200
    assert lst.json()["total"] >= 1

    fake_metrics = {
        "total_requests": 100,
        "failures": 1,
        "fail_ratio": 0.01,
        "rps": 33.3,
        "avg_ms": 40.0,
        "p50_ms": 30.0,
        "p90_ms": 50.0,
        "p95_ms": 60.0,
        "p99_ms": 80.0,
        "elapsed_sec": 3.0,
        "endpoints": [
            {
                "name": "GET /ping",
                "requests": 100,
                "failures": 1,
                "fail_ratio": 0.01,
                "avg_ms": 40.0,
                "p95_ms": 60.0,
                "rps": 33.3,
            }
        ],
    }

    with patch(
        "app.services.perf.perf_run_executor.run_locust_load",
        return_value=fake_metrics,
    ):
        rr = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 200, rr.text
    run = rr.json()
    assert run["status"] == "passed"
    assert run["summary_json"]["total_requests"] == 100
    assert run["summary_json"]["sla"]["passed"] is True
    run_id = run["id"]

    gr = client.get(f"/api/v1/projects/{pid}/perf-runs/{run_id}", headers=h)
    assert gr.status_code == 200
    assert gr.json()["id"] == run_id

    # async enqueue path
    with patch("app.tasks.perf.run_perf_scenario.run_perf_scenario_task.delay") as delay:
        delay.return_value = MagicMock(id="celery-perf-1")
        ar = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": False},
        )
    assert ar.status_code == 200
    assert ar.json()["celery_task_id"] == "celery-perf-1"
    assert ar.json()["status"] == "pending"

    # SLA fail path
    bad_metrics = {**fake_metrics, "p95_ms": 900.0, "fail_ratio": 0.5}
    with patch(
        "app.services.perf.perf_run_executor.run_locust_load",
        return_value=bad_metrics,
    ):
        fr = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert fr.status_code == 200
    assert fr.json()["status"] == "failed"
    assert fr.json()["summary_json"]["sla"]["passed"] is False

    # cross-project 404
    _user("perf_b", "p")
    tok_b = _login("perf_b", "p")
    pid_b = _project(tok_b, "PerfP2")
    hb = {"Authorization": f"Bearer {tok_b}"}
    nf = client.get(f"/api/v1/projects/{pid_b}/perf-scenarios/{scenario_id}", headers=hb)
    assert nf.status_code == 404
    nf2 = client.get(f"/api/v1/projects/{pid_b}/perf-runs/{run_id}", headers=hb)
    assert nf2.status_code == 404

    # delete requires admin — project creator is admin
    dr = client.delete(f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}", headers=h)
    assert dr.status_code == 204


def test_perf_weight_override_and_cancel(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("perf_c", "p")
    tok = _login("perf_c", "p")
    pid = _project(tok, "PerfCancel")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://api.example.com", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]

    c1 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "a", "method": "GET", "path": "/a", "status": "ready"},
    )
    c2 = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "b", "method": "GET", "path": "/b", "status": "ready"},
    )
    assert c1.status_code == 201 and c2.status_code == 201
    id1, id2 = c1.json()["id"], c2.json()["id"]

    sr = client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={
            "name": "weighted",
            "env_id": env_id,
            "case_items": [{"case_id": id1, "weight": 3}, {"case_id": id2, "weight": 1}],
            "users": 10,
            "spawn_rate": 2,
            "run_time_sec": 30,
        },
    )
    assert sr.status_code == 201
    scenario = sr.json()
    assert scenario["case_items"] == [{"case_id": id1, "weight": 3}, {"case_id": id2, "weight": 1}]
    scenario_id = scenario["id"]

    with patch("app.tasks.perf.run_perf_scenario.run_perf_scenario_task.delay") as delay:
        delay.return_value = MagicMock(id="celery-cancel-1")
        ar = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": False, "users": 20, "spawn_rate": 5, "run_time_sec": 15},
        )
    assert ar.status_code == 200
    run = ar.json()
    assert run["status"] == "pending"
    assert run["config_snapshot_json"]["users"] == 20
    assert run["config_snapshot_json"]["spawn_rate"] == 5
    assert run["config_snapshot_json"]["run_time_sec"] == 15
    assert run["config_snapshot_json"]["case_items"][0]["weight"] == 3
    run_id = run["id"]

    cr = client.post(f"/api/v1/projects/{pid}/perf-runs/{run_id}/cancel", headers=h)
    assert cr.status_code == 200
    assert cr.json()["status"] == "cancelled"
    assert cr.json()["finished_at"]

    again = client.post(f"/api/v1/projects/{pid}/perf-runs/{run_id}/cancel", headers=h)
    assert again.status_code == 400

    # running cancel + cancelled metrics
    with patch(
        "app.services.perf.perf_run_executor.run_locust_load",
        return_value={
            "total_requests": 10,
            "failures": 0,
            "fail_ratio": 0.0,
            "rps": 2.0,
            "avg_ms": 10.0,
            "p50_ms": 10.0,
            "p90_ms": 10.0,
            "p95_ms": 10.0,
            "p99_ms": 10.0,
            "elapsed_sec": 1.0,
            "endpoints": [],
            "cancelled": True,
        },
    ):
        rr = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert rr.status_code == 200
    assert rr.json()["status"] == "cancelled"
    assert rr.json()["summary_json"]["cancelled"] is True


def test_perf_compare_trends_export(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("perf_d", "p")
    tok = _login("perf_d", "p")
    pid = _project(tok, "PerfReport")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    er = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "local", "base_url": "https://api.example.com", "is_default": True},
    )
    assert er.status_code == 201
    env_id = er.json()["id"]
    cr = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={"name": "ping", "method": "GET", "path": "/ping", "status": "ready"},
    )
    assert cr.status_code == 201
    case_id = cr.json()["id"]
    sr = client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={
            "name": "trend-sc",
            "env_id": env_id,
            "case_items": [{"case_id": case_id, "weight": 1}],
            "users": 5,
            "run_time_sec": 5,
            "sla": {"max_p95_ms": 500},
        },
    )
    assert sr.status_code == 201
    scenario_id = sr.json()["id"]

    base_metrics = {
        "total_requests": 50,
        "failures": 0,
        "fail_ratio": 0.0,
        "rps": 20.0,
        "avg_ms": 40.0,
        "p50_ms": 30.0,
        "p90_ms": 50.0,
        "p95_ms": 60.0,
        "p99_ms": 80.0,
        "elapsed_sec": 5.0,
        "endpoints": [{"name": "GET /ping", "requests": 50, "failures": 0, "fail_ratio": 0, "avg_ms": 40, "p95_ms": 60, "rps": 20}],
    }
    cur_metrics = {**base_metrics, "p95_ms": 90.0, "rps": 18.0, "fail_ratio": 0.02, "failures": 1}

    with patch("app.services.perf.perf_run_executor.run_locust_load", return_value=base_metrics):
        r1 = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert r1.status_code == 200 and r1.json()["status"] == "passed"
    base_id = r1.json()["id"]

    with patch("app.services.perf.perf_run_executor.run_locust_load", return_value=cur_metrics):
        r2 = client.post(
            f"/api/v1/projects/{pid}/perf-scenarios/{scenario_id}/run",
            headers=h,
            json={"sync": True},
        )
    assert r2.status_code == 200 and r2.json()["status"] == "passed"
    cur_id = r2.json()["id"]

    cmp = client.get(f"/api/v1/projects/{pid}/perf-runs/{cur_id}/compare", headers=h)
    assert cmp.status_code == 200
    body = cmp.json()
    assert body["baseline_run_id"] == base_id
    p95 = next(m for m in body["metrics"] if m["metric"] == "p95_ms")
    assert p95["current"] == 90.0
    assert p95["baseline"] == 60.0
    assert p95["improved"] is False

    trends = client.get(f"/api/v1/projects/{pid}/perf-runs/trends?days=14&scenario_id={scenario_id}", headers=h)
    assert trends.status_code == 200
    tj = trends.json()
    assert tj["totals"]["passed"] >= 2
    assert any(p["run_id"] == cur_id for p in tj["points"])

    exp = client.get(f"/api/v1/projects/{pid}/perf-runs/{cur_id}/export?format=html", headers=h)
    assert exp.status_code == 200
    assert "text/html" in exp.headers.get("content-type", "")
    assert b"P95" in exp.content or b"p95" in exp.content.lower()
    assert str(cur_id).encode() in exp.content


def test_perf_list_search_and_status(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("perf_filt", "p")
    tok = _login("perf_filt", "p")
    pid = _project(tok, "PerfFilt")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    env = client.post(
        f"/api/v1/projects/{pid}/api-envs/",
        headers=h,
        json={"name": "PEnv", "base_url": "https://httpbin.org", "is_default": True},
    )
    assert env.status_code == 201, env.text
    env_id = env.json()["id"]

    case = client.post(
        f"/api/v1/projects/{pid}/api-cases/",
        headers=h,
        json={
            "name": "get",
            "method": "GET",
            "path": "/get",
            "assertions": [{"type": "status", "expected": 200}],
        },
    )
    assert case.status_code == 201, case.text
    case_id = case.json()["id"]

    sc = client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={
            "name": "LoadAlpha",
            "env_id": env_id,
            "case_items": [{"case_id": case_id, "weight": 1}],
            "users": 1,
            "spawn_rate": 1,
            "run_time_sec": 1,
        },
    )
    assert sc.status_code == 201, sc.text
    client.post(
        f"/api/v1/projects/{pid}/perf-scenarios/",
        headers=h,
        json={
            "name": "LoadBeta",
            "env_id": env_id,
            "case_items": [{"case_id": case_id, "weight": 1}],
            "users": 1,
            "spawn_rate": 1,
            "run_time_sec": 1,
        },
    )
    listed = client.get(f"/api/v1/projects/{pid}/perf-scenarios/?search=Alpha", headers=h)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["name"] == "LoadAlpha"

    runs = client.get(f"/api/v1/projects/{pid}/perf-runs/?status=pending", headers=h)
    assert runs.status_code == 200
    assert "items" in runs.json()
