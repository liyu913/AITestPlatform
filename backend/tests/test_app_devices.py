from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.identity.user import User
from app.services.identity import auth_service
from app.services.app_automation.app_device_pool import claim_device
from tests.conftest import TestingSessionLocal
from tests.test_app_automation import _FakeDriver


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


def _project(token: str, name: str = "DEV") -> int:
    r = TestClient(app).post(
        "/api/v1/projects/",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name},
    )
    assert r.status_code == 201
    return r.json()["id"]


def test_device_crud_heartbeat_release(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("dev_a", "p")
    tok = _login("dev_a", "p")
    pid = _project(tok, "DP1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    cr = client.post(
        f"/api/v1/projects/{pid}/app-devices/",
        headers=h,
        json={
            "name": "emu-1",
            "platform": "android",
            "udid": "emulator-5554",
            "device_name": "Pixel_6",
            "tags": ["smoke", "emu"],
            "status": "offline",
            "appium_server_url": "http://127.0.0.1:4723",
        },
    )
    assert cr.status_code == 201, cr.text
    device_id = cr.json()["id"]
    assert cr.json()["tags"] == ["smoke", "emu"]

    hb = client.post(
        f"/api/v1/projects/{pid}/app-devices/{device_id}/heartbeat",
        headers=h,
        json={"status": "online", "worker_id": "w1"},
    )
    assert hb.status_code == 200, hb.text
    assert hb.json()["status"] == "online"
    assert hb.json()["worker_id"] == "w1"
    assert hb.json()["last_heartbeat_at"]

    lst = client.get(f"/api/v1/projects/{pid}/app-devices/?tag=smoke", headers=h)
    assert lst.status_code == 200
    assert lst.json()["total"] == 1

    db = TestingSessionLocal()
    try:
        from app.models.app_automation.app_automation import AppDevice

        claimed = claim_device(db, project_id=pid, platform="android", required_tags=["smoke"], run_id=99)
        assert claimed is not None
        assert claimed.status == "busy"
        assert claimed.current_run_id == 99
        db.commit()
    finally:
        db.close()

    busy = client.get(f"/api/v1/projects/{pid}/app-devices/{device_id}", headers=h)
    assert busy.json()["status"] == "busy"

    rel = client.post(f"/api/v1/projects/{pid}/app-devices/{device_id}/release", headers=h, json={})
    assert rel.status_code == 200
    assert rel.json()["status"] == "online"
    assert rel.json()["current_run_id"] is None


def test_run_claims_and_releases_device(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("dev_b", "p")
    tok = _login("dev_b", "p")
    pid = _project(tok, "DP2")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    env = client.post(
        f"/api/v1/projects/{pid}/app-envs/",
        headers=h,
        json={
            "name": "local",
            "platform": "android",
            "app_package": "com.demo",
            "is_default": True,
            "use_device_pool": True,
            "device_tags": ["pool"],
        },
    )
    assert env.status_code == 201, env.text

    device = client.post(
        f"/api/v1/projects/{pid}/app-devices/",
        headers=h,
        json={
            "name": "d1",
            "platform": "android",
            "udid": "serial-1",
            "tags": ["pool"],
            "status": "online",
        },
    )
    assert device.status_code == 201, device.text
    device_id = device.json()["id"]

    case = client.post(
        f"/api/v1/projects/{pid}/app-cases/",
        headers=h,
        json={"name": "smoke", "steps": [{"action": "launch"}], "status": "ready"},
    )
    assert case.status_code == 201, case.text
    case_id = case.json()["id"]

    executed = []

    def fake_apply_async(args=None, countdown=None, queue=None, **kwargs):
        run_id = args[0]
        from app.services.app_automation.app_run_executor import execute_app_run
        from app.services.app_automation.app_runner import execute_app_steps as real_execute

        driver = _FakeDriver()
        db = TestingSessionLocal()
        try:
            with patch(
                "app.services.app_automation.app_run_executor.execute_app_steps",
                side_effect=lambda **kw: real_execute(**{**kw, "driver_factory": lambda: driver}),
            ):
                execute_app_run(db, run_id)
            executed.append(run_id)
        finally:
            db.close()
        m = MagicMock()
        m.id = f"app-task-{run_id}"
        return m

    with patch("app.tasks.app_automation.run_app_case.run_app_case_task.apply_async", side_effect=fake_apply_async):
        rr = client.post(
            f"/api/v1/projects/{pid}/app-cases/{case_id}/run",
            headers=h,
            json={"device_id": device_id},
        )
    assert rr.status_code == 201, rr.text
    run_id = rr.json()["id"]
    assert executed == [run_id]

    gr = client.get(f"/api/v1/projects/{pid}/app-runs/{run_id}", headers=h)
    assert gr.status_code == 200
    body = gr.json()
    assert body["status"] == "passed"
    assert body["device_id"] == device_id
    assert body["device_snapshot_json"]["udid"] == "serial-1"

    dev = client.get(f"/api/v1/projects/{pid}/app-devices/{device_id}", headers=h)
    assert dev.json()["status"] == "online"
    assert dev.json()["current_run_id"] is None


def test_claim_tag_mismatch(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("dev_tag", "p")
    tok = _login("dev_tag", "p")
    pid = _project(tok, "tagp")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    d = client.post(
        f"/api/v1/projects/{pid}/app-devices/",
        headers=h,
        json={"name": "a", "platform": "android", "tags": ["a"], "status": "online"},
    )
    assert d.status_code == 201

    db = TestingSessionLocal()
    try:
        claimed = claim_device(db, project_id=pid, platform="android", required_tags=["b"], run_id=1)
        assert claimed is None
    finally:
        db.close()


def test_parse_adb_devices_output():
    from app.services.app_automation.app_adb_scan import parse_adb_devices_output

    text = """List of devices attached
emulator-5554          device product:sdk_gphone64_x86_64 model:sdk_gphone64_x86_64 device:emu64xa transport_id:1
R58M123ABC             device usb:1-2 product:oriole model:Pixel_6 device:oriole transport_id:2
offline-x              offline
unauth-y               unauthorized
"""
    items = parse_adb_devices_output(text)
    assert len(items) == 4
    assert items[0].serial == "emulator-5554"
    assert items[0].state == "device"
    assert items[1].model == "Pixel 6"
    assert items[2].state == "offline"
    assert items[3].state == "unauthorized"


def test_scan_syncs_devices(monkeypatch):
    from app.services.app_automation.app_adb_scan import AdbDeviceInfo

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)

    _user("dev_scan", "p")
    tok = _login("dev_scan", "p")
    pid = _project(tok, "SCAN1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    # existing adb-tagged device that will go missing → offline
    old = client.post(
        f"/api/v1/projects/{pid}/app-devices/",
        headers=h,
        json={
            "name": "gone",
            "platform": "android",
            "udid": "old-serial",
            "tags": ["adb"],
            "status": "online",
        },
    )
    assert old.status_code == 201

    fake = [
        AdbDeviceInfo(
            serial="emulator-5554",
            state="device",
            model="sdk gphone",
            platform_version="14",
        ),
        AdbDeviceInfo(serial="bad", state="unauthorized"),
    ]

    with patch("app.services.app_automation.app_adb_scan.fetch_adb_devices", return_value=fake):
        sr = client.post(
            f"/api/v1/projects/{pid}/app-devices/scan",
            headers=h,
            json={"mark_missing_offline": True},
        )
    assert sr.status_code == 200, sr.text
    body = sr.json()
    assert body["ready"] == 1
    assert body["created"] >= 1
    assert body["marked_offline"] >= 1
    assert any(d["udid"] == "emulator-5554" and d["status"] == "online" for d in body["devices"])

    lst = client.get(f"/api/v1/projects/{pid}/app-devices/", headers=h)
    by_udid = {d["udid"]: d for d in lst.json()["items"]}
    assert by_udid["emulator-5554"]["status"] == "online"
    assert "adb" in by_udid["emulator-5554"]["tags"]
    assert by_udid["old-serial"]["status"] == "offline"


def test_scan_adb_not_found(monkeypatch):
    from app.services.app_automation.app_adb_scan import AdbNotFoundError

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("dev_scan2", "p")
    tok = _login("dev_scan2", "p")
    pid = _project(tok, "SCAN2")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    with patch(
        "app.services.app_automation.app_adb_scan.fetch_adb_devices",
        side_effect=AdbNotFoundError("no adb"),
    ):
        sr = client.post(f"/api/v1/projects/{pid}/app-devices/scan", headers=h, json={})
    assert sr.status_code == 400


def test_device_list_search(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "settings_encryption_key", key)
    _user("dev_search", "p")
    tok = _login("dev_search", "p")
    pid = _project(tok, "DS1")
    client = TestClient(app)
    h = {"Authorization": f"Bearer {tok}"}

    client.post(
        f"/api/v1/projects/{pid}/app-devices/",
        headers=h,
        json={
            "name": "PixelOne",
            "platform": "android",
            "udid": "serial-aaa",
            "status": "offline",
            "appium_server_url": "http://127.0.0.1:4723",
        },
    )
    client.post(
        f"/api/v1/projects/{pid}/app-devices/",
        headers=h,
        json={
            "name": "PixelTwo",
            "platform": "android",
            "udid": "serial-bbb",
            "status": "offline",
            "appium_server_url": "http://127.0.0.1:4723",
        },
    )
    by_name = client.get(f"/api/v1/projects/{pid}/app-devices/?search=PixelOne", headers=h)
    assert by_name.status_code == 200
    assert by_name.json()["total"] == 1
    assert by_name.json()["items"][0]["name"] == "PixelOne"

    by_udid = client.get(f"/api/v1/projects/{pid}/app-devices/?search=serial-bbb", headers=h)
    assert by_udid.status_code == 200
    assert by_udid.json()["total"] == 1
    assert by_udid.json()["items"][0]["udid"] == "serial-bbb"
