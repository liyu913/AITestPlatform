"""Drive @playwright/cli named sessions for product UI explore (CDP + optional Midscene)."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_RUNNER_DIR = Path(__file__).resolve().parents[4] / "ui-runner"
_REF = re.compile(r"^(?:ref=)?(e\d+)$", re.I)
_LOCATOR_REF = re.compile(
    r"""^(?:page\.)?locator\(\s*['\"](?:ref=)?e\d+['\"]\s*\)$""",
    re.I,
)
_LOCATOR_LINE = re.compile(
    r"(page\.(?:getBy(?:Role|Label|Placeholder|Text|TestId)|locator)\([^;\n]+\)(?:\.[a-zA-Z]+\([^)]*\))?)",
    re.I,
)


def cli_ready() -> str | None:
    cli_pkg = _RUNNER_DIR / "node_modules" / "@playwright" / "cli"
    if not cli_pkg.is_dir():
        return (
            f"未安装 @playwright/cli（缺少 {cli_pkg}）。"
            f"请在 {_RUNNER_DIR} 目录执行：npm install"
        )
    return None


def is_element_ref(target: str | None) -> bool:
    return bool(target and _REF.match(str(target).strip()))


def is_snapshot_ref_locator(raw: str | None) -> bool:
    """True for live snapshot refs like e23 / page.locator("e23") — not stable in saved scripts."""
    s = (raw or "").strip().rstrip(";").strip()
    if not s:
        return False
    if is_element_ref(s):
        return True
    return bool(_LOCATOR_REF.match(s))


def normalize_ref(target: str) -> str:
    m = _REF.match(str(target).strip())
    if not m:
        raise ValueError(f"不是有效的 snapshot ref: {target!r}")
    return m.group(1).lower()


class PlaywrightCliSession:
    """One named playwright-cli browser session for a single explore job.

    Prefer CDP-backed Chromium so Midscene can attach to the same page for captcha aiQuery.
    """

    def __init__(
        self,
        *,
        headed: bool = False,
        session_name: str | None = None,
        timeout_ms: int = 15000,
        cwd: Path | None = None,
        use_cdp: bool = True,
        storage_state: dict[str, Any] | str | None = None,
        extra_http_headers: dict[str, str] | None = None,
        local_storage: dict[str, str] | None = None,
    ) -> None:
        missing = cli_ready()
        if missing:
            raise RuntimeError(missing)
        self.session_name = session_name or f"raina-{uuid.uuid4().hex[:10]}"
        self.headed = headed
        self.timeout_ms = max(3000, int(timeout_ms))
        self.cwd = Path(cwd or _RUNNER_DIR)
        self.use_cdp = use_cdp
        self.storage_state = storage_state
        self.extra_http_headers = extra_http_headers
        self.local_storage = local_storage
        self.cdp_endpoint: str | None = None
        self._opened = False
        self._pw = None
        self._browser = None
        self._context = None

    def _context_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        if self.storage_state:
            opts["storage_state"] = self.storage_state
        if self.extra_http_headers:
            opts["extra_http_headers"] = dict(self.extra_http_headers)
        return opts

    def _argv(self, *parts: str) -> list[str]:
        return [
            "npx",
            "--no-install",
            "playwright-cli",
            f"-s={self.session_name}",
            "--raw",
            *parts,
        ]

    def run(self, *parts: str, check: bool = True) -> str:
        cmd = self._argv(*parts)
        log.debug("playwright-cli %s", " ".join(parts))
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30, self.timeout_ms // 1000 + 20),
            )
        except subprocess.TimeoutExpired as ex:
            raise RuntimeError(f"playwright-cli 超时: {' '.join(parts)}") from ex
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if check and proc.returncode != 0:
            raise RuntimeError(
                (err or out or f"playwright-cli exit {proc.returncode}")[-2000:]
            )
        return out

    def _open_via_cdp(self, url: str) -> None:
        from app.services.ui_automation.node_ui_runner import (
            cdp_endpoint_for_port,
            chromium_launch_args,
            free_cdp_port,
        )
        from playwright.sync_api import sync_playwright

        port = free_cdp_port()
        endpoint = cdp_endpoint_for_port(port)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=not self.headed,
            args=chromium_launch_args(cdp_port=port),
        )
        # Warm a page so Midscene / CLI have a target after attach.
        context = self._browser.new_context(**self._context_options())
        from app.services.ui_automation.ui_env_auth import local_storage_init_script

        init_js = local_storage_init_script(self.local_storage)
        if init_js:
            context.add_init_script(init_js)
        self._context = context
        page = context.new_page()
        page.goto("about:blank", wait_until="domcontentloaded", timeout=self.timeout_ms)
        self.cdp_endpoint = endpoint
        # Wait briefly for CDP HTTP to accept connections
        deadline = time.monotonic() + 8
        last_err = ""
        while time.monotonic() < deadline:
            try:
                self.run("attach", f"--cdp={endpoint}")
                last_err = ""
                break
            except Exception as ex:  # noqa: BLE001
                last_err = str(ex)
                time.sleep(0.25)
        if last_err:
            raise RuntimeError(f"playwright-cli attach CDP 失败: {last_err}") from None
        self.run("goto", url)
        self._opened = True

    def open(self, url: str) -> None:
        if self.use_cdp:
            try:
                self._open_via_cdp(url)
                return
            except Exception as ex:  # noqa: BLE001
                log.warning("CDP open failed, falling back to playwright-cli open: %s", ex)
                self._shutdown_cdp_browser()
                self.cdp_endpoint = None
        args = ["open", url]
        if self.headed:
            args.append("--headed")
        self.run(*args)
        self._opened = True

    def goto(self, url: str) -> None:
        self.run("goto", url)

    def snapshot(self, *, depth: int | None = 8) -> str:
        parts = ["snapshot"]
        if depth is not None:
            parts.append(f"--depth={int(depth)}")
        return self.run(*parts)

    def screenshot(self) -> bytes:
        """PNG bytes of the current page. Prefer live CDP context; else playwright-cli."""
        page = None
        if self._context is not None:
            try:
                pages = list(self._context.pages)
                if pages:
                    page = pages[-1]
            except Exception:  # noqa: BLE001
                page = None
        if page is not None:
            try:
                data = page.screenshot(type="png", full_page=False)
                return bytes(data) if data else b""
            except Exception as ex:  # noqa: BLE001
                log.warning("context screenshot failed: %s", ex)
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", prefix="explore-", delete=False) as tmp:
                tmp_path = tmp.name
            self.run("screenshot", tmp_path)
            data = Path(tmp_path).read_bytes()
            return data if data else b""
        except Exception as ex:  # noqa: BLE001
            log.warning("playwright-cli screenshot failed: %s", ex)
            return b""
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def click(self, target: str) -> None:
        t = normalize_ref(target) if is_element_ref(target) else target
        self.run("click", t)

    def fill(self, target: str, text: str) -> None:
        t = normalize_ref(target) if is_element_ref(target) else target
        self.run("fill", t, text)

    def press(self, key: str) -> None:
        self.run("press", key)

    def dump_storage_state(self) -> dict[str, Any] | None:
        if self._context is None:
            return None
        try:
            state = self._context.storage_state()
        except Exception:  # noqa: BLE001
            return None
        return state if isinstance(state, dict) else None

    def generate_locator(self, target: str) -> str:
        t = normalize_ref(target) if is_element_ref(target) else target
        raw = self.run("generate-locator", t)
        found = _LOCATOR_LINE.search(raw.replace("\n", " "))
        expr = found.group(1).strip() if found else (
            raw.strip().splitlines()[0].strip() if raw.strip() else ""
        )
        if is_snapshot_ref_locator(expr):
            return ""
        return expr

    def _shutdown_cdp_browser(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        self._context = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    def close(self) -> None:
        if self._opened:
            try:
                if self.cdp_endpoint:
                    self.run("detach", check=False)
                else:
                    self.run("close", check=False)
            except Exception:  # noqa: BLE001
                pass
            self._opened = False
        else:
            try:
                self.run("close", check=False)
            except Exception:  # noqa: BLE001
                pass
        self._shutdown_cdp_browser()
        self.cdp_endpoint = None
        try:
            self.run("delete-data", check=False)
        except Exception:  # noqa: BLE001
            pass


def join_explore_url(base_url: str, start_path: str) -> str:
    base = (base_url or "").rstrip("/")
    path = start_path if str(start_path).startswith("/") else f"/{start_path or ''}"
    if not path:
        path = "/"
    return f"{base}{path}"


def unique_session_name(prefix: str = "raina") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
