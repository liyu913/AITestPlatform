"""Spawn the Node Playwright + Midscene runner."""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_RUNNER_DIR = Path(__file__).resolve().parents[4] / "ui-runner"
_RESULT_PREFIX = "RAINAHUB_RESULT "
_LOG_PREFIX = "RAINAHUB_LOG "
_STEP_PREFIX = "RAINAHUB_STEP "
_NEED_HUMAN_PREFIX = "RAINAHUB_NEED_HUMAN "
_RESUME_PREFIX = "RAINAHUB_RESUME "
_ABORT_LINE = "RAINAHUB_ABORT"
# Align with Midscene default; consecutive fail early-stop lives in explore.mjs.
_DEFAULT_REPLANNING_CYCLE_LIMIT = 20
MANUAL_STOP_MESSAGE = "已手动停止探索"


def _active_model_config() -> dict[str, str]:
    from app.deps import SessionLocal
    from app.services.ai.effective_ai_settings import get_midscene_runtime

    db = SessionLocal()
    try:
        return get_midscene_runtime(db).as_model_config()
    finally:
        db.close()


def runner_dir() -> Path:
    return _RUNNER_DIR


def free_cdp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def cdp_endpoint_for_port(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}"


def chromium_launch_args(*, cdp_port: int | None = None) -> list[str]:
    args = ["--no-sandbox", "--disable-setuid-sandbox"]
    if cdp_port:
        args.append(f"--remote-debugging-port={int(cdp_port)}")
    return args


def _node_ready(*, script_name: str = "explore.mjs") -> str | None:
    script = _RUNNER_DIR / script_name
    modules = _RUNNER_DIR / "node_modules" / "@midscene" / "web"
    playwright_test = _RUNNER_DIR / "node_modules" / "@playwright" / "test"
    if not script.is_file():
        return f"未找到 {script}"
    if not modules.is_dir() or not playwright_test.is_dir():
        missing = modules if not modules.is_dir() else playwright_test
        return (
            f"未安装 ui-runner 依赖（缺少 {missing}）。"
            f"请在 {_RUNNER_DIR} 目录执行：npm install && npx playwright install chromium"
        )
    return None


def _run_node(
    script_name: str,
    payload: dict[str, Any],
    *,
    timeout_sec: int,
    on_log: Callable[[str], None] | None = None,
    on_step_event: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_need_human: Callable[[str], str | None] | None = None,
    keep_stdin_open: bool = False,
    raise_on_fail: bool = True,
) -> dict[str, Any]:
    missing = _node_ready(script_name=script_name)
    if missing:
        raise RuntimeError(missing)

    env = os.environ.copy()
    env.setdefault("MIDSCENE_REPLANNING_CYCLE_LIMIT", str(_DEFAULT_REPLANNING_CYCLE_LIMIT))
    proc = subprocess.Popen(
        ["node", str(_RUNNER_DIR / script_name)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_RUNNER_DIR),
        bufsize=1,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    try:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        if not keep_stdin_open:
            proc.stdin.close()
    except BrokenPipeError:
        pass

    result: dict[str, Any] | None = None
    stdout_tail: list[str] = []
    stderr = ""
    deadline = time.monotonic() + max(1, int(timeout_sec))
    cancelled = False
    try:
        while True:
            if should_cancel is not None and should_cancel():
                cancelled = True
                if keep_stdin_open and proc.stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.write(f"{_ABORT_LINE}\n")
                        proc.stdin.flush()
                    except BrokenPipeError:
                        pass
                proc.kill()
                break
            if time.monotonic() > deadline:
                if keep_stdin_open and proc.stdin and not proc.stdin.closed:
                    try:
                        proc.stdin.write(f"{_ABORT_LINE}\n")
                        proc.stdin.flush()
                    except BrokenPipeError:
                        pass
                proc.kill()
                raise TimeoutError(f"node {script_name} 超时（{timeout_sec}s）")
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if not line:
                time.sleep(0.05)
                continue
            text = line.rstrip("\n")
            stdout_tail.append(text)
            if len(stdout_tail) > 200:
                stdout_tail = stdout_tail[-200:]
            if text.startswith(_LOG_PREFIX):
                if on_log is not None:
                    try:
                        data = json.loads(text[len(_LOG_PREFIX) :])
                        msg = str(data.get("message") or "").strip()
                    except Exception:  # noqa: BLE001
                        msg = text[len(_LOG_PREFIX) :].strip()
                    if msg:
                        on_log(msg[:500])
                continue
            if text.startswith(_STEP_PREFIX):
                if on_step_event is not None:
                    try:
                        data = json.loads(text[len(_STEP_PREFIX) :])
                    except Exception:  # noqa: BLE001
                        data = None
                    if isinstance(data, dict):
                        try:
                            on_step_event(data)
                        except Exception:  # noqa: BLE001
                            log.exception("on_step_event failed")
                continue
            if text.startswith(_NEED_HUMAN_PREFIX):
                reason = ""
                try:
                    data = json.loads(text[len(_NEED_HUMAN_PREFIX) :])
                    reason = str(data.get("hint") or data.get("reason") or "").strip()
                except Exception:  # noqa: BLE001
                    reason = text[len(_NEED_HUMAN_PREFIX) :].strip()
                if on_log is not None and reason:
                    on_log(reason[:500])
                reply: str | None = None
                if on_need_human is not None:
                    try:
                        reply = on_need_human(reason)
                    except Exception:  # noqa: BLE001
                        log.exception("on_need_human failed")
                        reply = None
                if keep_stdin_open and proc.stdin and not proc.stdin.closed:
                    try:
                        if reply is None:
                            proc.stdin.write(f"{_ABORT_LINE}\n")
                        else:
                            proc.stdin.write(
                                f"{_RESUME_PREFIX}{json.dumps({'text': reply}, ensure_ascii=False)}\n"
                            )
                        proc.stdin.flush()
                    except BrokenPipeError:
                        pass
                continue
            if text.startswith(_RESULT_PREFIX):
                try:
                    result = json.loads(text[len(_RESULT_PREFIX) :])
                except json.JSONDecodeError:
                    result = None
        stderr = proc.stderr.read() or ""
    finally:
        if keep_stdin_open and proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()

    if cancelled:
        raise RuntimeError(MANUAL_STOP_MESSAGE)
    if result is None:
        err = (stderr or "\n".join(stdout_tail)).strip()
        raise RuntimeError(err[-2000:] or f"node {script_name} 没有返回结果 (exit={proc.returncode})")
    if not result.get("ok") and raise_on_fail:
        raise RuntimeError(str(result.get("error") or "Node 执行失败")[-2000:])
    return result


def midscene_step_over_cdp(
    *,
    cdp_endpoint: str,
    action: str,
    prompt: str,
    value: str = "",
    model_config: dict[str, str] | None = None,
    timeout_ms: int = 15000,
    on_log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run one aiTap/aiInput/aiQuery on a Chromium page via CDP (same browser as CLI)."""
    timeout_sec = max(60, int(timeout_ms / 1000) * 4)
    result = _run_node(
        "step.mjs",
        {
            "cdpEndpoint": cdp_endpoint,
            "action": action,
            "prompt": prompt,
            "value": value,
            "timeoutMs": timeout_ms,
            "modelConfig": model_config or _active_model_config(),
        },
        timeout_sec=timeout_sec,
        on_log=on_log,
    )
    return result if isinstance(result, dict) else {"ok": True}


def midscene_query_over_cdp(
    *,
    cdp_endpoint: str,
    prompt: str,
    model_config: dict[str, str] | None = None,
    timeout_ms: int = 15000,
    on_log: Callable[[str], None] | None = None,
) -> str:
    """Read captcha (or other) text via Midscene aiQuery on the live page."""
    result = midscene_step_over_cdp(
        cdp_endpoint=cdp_endpoint,
        action="query",
        prompt=prompt,
        model_config=model_config,
        timeout_ms=timeout_ms,
        on_log=on_log,
    )
    return str(result.get("text") or "").strip()


def explore_with_midscene(
    *,
    base_url: str,
    start_path: str,
    task: str,
    model_config: dict[str, str],
    browser: str,
    headed: bool,
    viewport: dict[str, int] | None,
    timeout_ms: int,
    on_log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_need_human: Callable[[str], str | None] | None = None,
) -> list[dict[str, Any]]:
    # Extra budget for headed human-assist waits (up to ~15 min each, max 3).
    timeout_sec = max(120, int(timeout_ms / 1000) * 8)
    if headed and on_need_human is not None:
        timeout_sec = max(timeout_sec, 60 * 50)
    result = _run_node(
        "explore.mjs",
        {
            "baseUrl": base_url,
            "startPath": start_path or "/",
            "task": task,
            "modelConfig": model_config,
            "browser": browser,
            "headed": headed,
            "viewport": viewport or {"width": 1280, "height": 720},
            "timeoutMs": timeout_ms,
            "replanningCycleLimit": _DEFAULT_REPLANNING_CYCLE_LIMIT,
        },
        timeout_sec=timeout_sec,
        on_log=on_log,
        should_cancel=should_cancel,
        on_need_human=on_need_human if headed else None,
        keep_stdin_open=bool(headed and on_need_human is not None),
    )
    actions = result.get("actions")
    return actions if isinstance(actions, list) else []


def _script_env(ctx: dict[str, str] | None) -> dict[str, str]:
    """Always provide string username/password so Playwright fill() never gets undefined."""
    env = {str(k): str(v) for k, v in (ctx or {}).items() if v is not None}
    env.setdefault("username", "")
    env.setdefault("password", "")
    env.setdefault("user", env["username"])
    env.setdefault("passwd", env["password"])
    env.setdefault("LOGIN_USERNAME", env["username"])
    env.setdefault("LOGIN_PASSWORD", env["password"])
    return env


def _is_playwright_expect_failure(error: str) -> bool:
    text = error or ""
    return "expect(" in text or "toHaveURL" in text or "toBeVisible" in text or "toHaveText" in text


def _save_node_screenshot(
    result: dict[str, Any],
    screenshot_saver: Callable[[bytes, str], str] | None,
    *,
    filename: str = "fail_script.png",
) -> str | None:
    if screenshot_saver is None:
        return None
    raw = str(result.get("screenshotBase64") or "").strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw)
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return None
    try:
        return screenshot_saver(data, filename)
    except Exception:  # noqa: BLE001
        log.warning("save node screenshot failed (%s)", filename, exc_info=True)
        return None


def _node_script_row(
    result: dict[str, Any],
    *,
    status: str,
    error_message: str = "",
    screenshot_saver: Callable[[bytes, str], str] | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    ok = status == "passed"
    shot = _save_node_screenshot(
        result,
        screenshot_saver,
        filename="ok_script.png" if ok else "fail_script.png",
    )
    detail: dict[str, Any] = {}
    url = str(result.get("url") or "").strip()
    if url:
        detail["url"] = url
    return {
        "action": "script",
        "status": status,
        "error_message": error_message,
        "selector": "",
        "screenshot_key": shot,
        "detail_json": detail or None,
        "duration_ms": duration_ms,
        "retry_count": 0,
    }


def _node_event_to_step(
    event: dict[str, Any],
    *,
    screenshot_saver: Callable[[bytes, str], str] | None,
) -> dict[str, Any]:
    status = str(event.get("status") or "passed").strip() or "passed"
    order = int(event.get("order") or 0)
    ok = status == "passed"
    filename = f"step_{order}.png" if ok else f"fail_{order}.png"
    shot = _save_node_screenshot(event, screenshot_saver, filename=filename)
    detail: dict[str, Any] = {}
    url = str(event.get("url") or "").strip()
    if url:
        detail["url"] = url
    return {
        "order": order,
        "action": str(event.get("action") or "script"),
        "selector": str(event.get("selector") or ""),
        "status": status,
        "error_message": str(event.get("error") or ""),
        "duration_ms": event.get("durationMs"),
        "screenshot_key": shot,
        "detail_json": detail or None,
        "retry_count": 0,
    }


def execute_node_ui_script(
    *,
    base_url: str,
    script: str,
    ctx: dict[str, str],
    viewport: dict[str, int] | None = None,
    headless: bool = True,
    browser_name: str = "chromium",
    default_timeout_ms: int = 15000,
    model_config: dict[str, str] | None = None,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
    screenshot_saver: Callable[[bytes, str], str] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run ``async function run(page, agent)``. Emits per-action steps when available."""
    payload: dict[str, Any] = {
        "baseUrl": base_url,
        "script": script,
        "env": _script_env(ctx),
        "browser": browser_name,
        "headless": headless,
        "viewport": viewport or {"width": 1280, "height": 720},
        "timeoutMs": default_timeout_ms,
        "modelConfig": model_config or _active_model_config(),
    }
    if storage_state_path:
        payload["storageStatePath"] = str(storage_state_path)
    if save_storage_state_path:
        payload["saveStorageStatePath"] = str(save_storage_state_path)
    if extra_http_headers:
        payload["extraHTTPHeaders"] = {str(k): str(v) for k, v in extra_http_headers.items()}
    if local_storage:
        payload["localStorage"] = {str(k): str(v) for k, v in local_storage.items()}
    t0 = time.perf_counter()
    step_rows: list[dict[str, Any]] = []

    def _on_step_event(event: dict[str, Any]) -> None:
        row = _node_event_to_step(event, screenshot_saver=screenshot_saver)
        step_rows.append(row)
        if on_step is not None:
            on_step(row)

    try:
        result = _run_node(
            "run.mjs",
            payload,
            timeout_sec=max(120, int(default_timeout_ms / 1000) * 6),
            raise_on_fail=False,
            on_log=on_log,
            on_step_event=_on_step_event,
        )
    except Exception as ex:  # noqa: BLE001
        log.info("node ui script failed: %s", ex)
        row = {
            "action": "script",
            "status": "error",
            "error_message": str(ex),
            "selector": "",
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "retry_count": 0,
        }
        if on_step is not None and not step_rows:
            on_step(row)
        return step_rows or [row]
    duration_ms = int((time.perf_counter() - t0) * 1000)
    if result.get("ok"):
        if step_rows:
            return step_rows
        row = _node_script_row(
            result,
            status="passed",
            screenshot_saver=screenshot_saver,
            duration_ms=duration_ms,
        )
        if on_step is not None:
            on_step(row)
        return [row]
    error = str(result.get("error") or "Node 执行失败")[-2000:]
    status = str(result.get("status") or "").strip()
    if status not in ("failed", "error"):
        status = "failed" if _is_playwright_expect_failure(error) else "error"
    if step_rows:
        if str(step_rows[-1].get("status") or "") == "passed":
            row = _node_script_row(
                result,
                status=status,
                error_message=error,
                screenshot_saver=screenshot_saver,
                duration_ms=duration_ms,
            )
            step_rows.append(row)
            if on_step is not None:
                on_step(row)
        return step_rows
    row = _node_script_row(
        result,
        status=status,
        error_message=error,
        screenshot_saver=screenshot_saver,
        duration_ms=duration_ms,
    )
    if on_step is not None:
        on_step(row)
    return [row]
