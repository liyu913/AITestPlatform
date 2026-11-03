"""Scan local Android devices via adb (manual trigger, no cron)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.core.datetime_json import now_beijing, to_beijing_iso
from app.models.app_automation.app_automation import AppDevice

log = logging.getLogger(__name__)

_ADB_LINE_RE = re.compile(
    r"^(?P<serial>\S+)\s+(?P<state>device|offline|unauthorized|no\s+permissions|unknown)"
    r"(?:\s+(?P<rest>.+))?$"
)
_KV_RE = re.compile(r"(\w+):(\S+)")
ADB_SCAN_TAG = "adb"


class AdbNotFoundError(RuntimeError):
    pass


class AdbCommandError(RuntimeError):
    pass


@dataclass
class AdbDeviceInfo:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""
    platform_version: str = ""


def _utc_now_iso() -> str:
    return to_beijing_iso(now_beijing())


def resolve_adb_bin(explicit: str | None = None) -> str:
    candidate = (explicit or getattr(settings, "adb_bin", None) or "adb").strip() or "adb"
    if "/" in candidate or "\\" in candidate:
        return candidate
    found = shutil.which(candidate)
    if found:
        return found
    raise AdbNotFoundError(
        f"未找到 adb 可执行文件（当前配置: {candidate}）。"
        "请安装 Android Platform-Tools 并加入 PATH，或设置 ADB_BIN。"
    )


def parse_adb_devices_output(text: str) -> list[AdbDeviceInfo]:
    out: list[AdbDeviceInfo] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices"):
            continue
        m = _ADB_LINE_RE.match(line)
        if not m:
            continue
        rest = m.group("rest") or ""
        kvs = dict(_KV_RE.findall(rest))
        out.append(
            AdbDeviceInfo(
                serial=m.group("serial"),
                state=m.group("state").replace(" ", "_"),
                model=kvs.get("model", "").replace("_", " "),
                product=kvs.get("product", ""),
                device=kvs.get("device", ""),
                transport_id=kvs.get("transport_id", ""),
            )
        )
    return out


def _run_adb(args: list[str], *, adb_bin: str, timeout: float = 15.0) -> str:
    try:
        proc = subprocess.run(
            [adb_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as ex:
        raise AdbNotFoundError(str(ex)) from ex
    except subprocess.TimeoutExpired as ex:
        raise AdbCommandError(f"adb 命令超时: {' '.join(args)}") from ex
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise AdbCommandError(err or f"adb 退出码 {proc.returncode}")
    return proc.stdout or ""


def fetch_adb_devices(
    *,
    adb_bin: str | None = None,
    enrich: bool = True,
    runner: Callable[..., str] | None = None,
) -> list[AdbDeviceInfo]:
    bin_path = resolve_adb_bin(adb_bin)
    run = runner or (lambda args, **kw: _run_adb(args, adb_bin=bin_path, **kw))
    raw = run(["devices", "-l"])
    devices = parse_adb_devices_output(raw)
    if not enrich:
        return devices
    for d in devices:
        if d.state != "device":
            continue
        try:
            model = run(["-s", d.serial, "shell", "getprop", "ro.product.model"], timeout=8.0).strip()
            if model:
                d.model = model
        except Exception:  # noqa: BLE001
            log.debug("getprop model failed for %s", d.serial, exc_info=True)
        try:
            ver = run(
                ["-s", d.serial, "shell", "getprop", "ro.build.version.release"],
                timeout=8.0,
            ).strip()
            if ver:
                d.platform_version = ver
        except Exception:  # noqa: BLE001
            log.debug("getprop version failed for %s", d.serial, exc_info=True)
    return devices


def _ensure_adb_tag(tags: list | None) -> list[str]:
    out = [str(t) for t in (tags or []) if str(t).strip()]
    if ADB_SCAN_TAG not in out:
        out.append(ADB_SCAN_TAG)
    return out


def sync_adb_devices_to_pool(
    db: Session,
    *,
    project_id: int,
    creator_id: int | None,
    mark_missing_offline: bool = True,
    adb_bin: str | None = None,
    devices: list[AdbDeviceInfo] | None = None,
) -> dict:
    """Upsert scanned adb devices into app_devices. Returns summary counts."""
    scanned = devices if devices is not None else fetch_adb_devices(adb_bin=adb_bin)
    ready = [d for d in scanned if d.state == "device"]
    seen_serials = {d.serial for d in ready}

    created = updated = online = skipped = marked_offline = 0
    items: list[AppDevice] = []

    existing = (
        db.query(AppDevice)
        .filter(AppDevice.project_id == project_id, AppDevice.platform == "android")
        .all()
    )
    by_udid = {(r.udid or "").strip(): r for r in existing if (r.udid or "").strip()}

    for info in ready:
        row = by_udid.get(info.serial)
        display_name = info.model or info.device or info.serial
        if row is None:
            row = AppDevice(
                project_id=project_id,
                name=display_name[:128],
                platform="android",
                udid=info.serial,
                device_name=display_name[:255],
                platform_version=info.platform_version or "",
                automation_name="UiAutomator2",
                tags_json=[ADB_SCAN_TAG],
                status="online",
                worker_id="adb-scan",
                last_heartbeat_at=_utc_now_iso(),
                notes="由 adb 扫描自动注册",
                creator_id=creator_id,
            )
            db.add(row)
            created += 1
            online += 1
        else:
            if row.status == "busy":
                # keep busy; still refresh metadata
                skipped += 1
            else:
                row.status = "online"
                online += 1
            if info.model:
                row.device_name = info.model[:255]
                if not row.name or row.name == row.udid:
                    row.name = info.model[:128]
            if info.platform_version:
                row.platform_version = info.platform_version[:64]
            row.tags_json = _ensure_adb_tag(row.tags_json if isinstance(row.tags_json, list) else [])
            row.last_heartbeat_at = _utc_now_iso()
            if not row.worker_id:
                row.worker_id = "adb-scan"
            updated += 1
        items.append(row)

    if mark_missing_offline:
        for row in existing:
            udid = (row.udid or "").strip()
            if not udid or udid in seen_serials:
                continue
            tags = row.tags_json if isinstance(row.tags_json, list) else []
            if ADB_SCAN_TAG not in [str(t) for t in tags]:
                continue
            if row.status in ("busy", "disabled"):
                continue
            if row.status != "offline":
                row.status = "offline"
                row.current_run_id = None
                marked_offline += 1

    db.commit()
    for row in items:
        db.refresh(row)

    return {
        "scanned": len(scanned),
        "ready": len(ready),
        "created": created,
        "updated": updated,
        "online": online,
        "skipped_busy": skipped,
        "marked_offline": marked_offline,
        "devices": items,
        "raw": [
            {
                "serial": d.serial,
                "state": d.state,
                "model": d.model,
                "platform_version": d.platform_version,
            }
            for d in scanned
        ],
    }
