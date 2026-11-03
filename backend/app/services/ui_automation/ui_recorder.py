"""Headed Playwright recorder: open the site, capture steps, compile a script on stop."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.ui_automation.ui_automation import UiEnvironment
from app.services.ui_automation.ui_ai_explorer import steps_to_script
from app.services.ui_automation.ui_runner import _launch_browser
from app.user_messages import (
    UI_ENV_BASE_URL_REQUIRED,
    UI_ENV_NOT_FOUND,
    UI_RECORDING_BUSY,
    UI_RECORDING_FAILED,
    UI_RECORDING_NOT_FOUND,
)

log = logging.getLogger(__name__)

LAUNCH_TIMEOUT_S = 45
STOP_TIMEOUT_S = 20
_MAX_SESSIONS = 20

_ALLOWED = {"goto", "click", "fill", "select_option", "press", "hover"}

_lock = threading.Lock()
_sessions: dict[str, "RecordSession"] = {}


def normalize_recorded_step(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip()
    if action not in _ALLOWED:
        return None
    selector = str(raw.get("selector") or "").strip()[:1024]
    value = raw.get("value")
    if value is not None:
        value = str(value)[:4000]
    if action == "goto":
        path = str(value or "").strip()
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            return None
        if ".." in path.split("/"):
            return None
        return {"action": "goto", "value": path}
    if action in ("click", "hover"):
        if not selector:
            return None
        return {"action": action, "selector": selector}
    if value is None or str(value) == "":
        return None
    if not selector:
        return None
    return {"action": action, "selector": selector, "value": str(value)}


def to_relative_path(base_url: str, href: str) -> str | None:
    try:
        base = urlparse(base_url)
        cur = urlparse(href)
    except ValueError:
        return None
    if cur.scheme not in ("http", "https") or not cur.netloc:
        return None
    if (cur.hostname or "").lower() != (base.hostname or "").lower():
        return None
    if (cur.port or _default_port(cur.scheme)) != (base.port or _default_port(base.scheme)):
        return None
    path = cur.path or "/"
    base_path = (base.path or "/").rstrip("/")
    if base_path and base_path != "/" and (path == base_path or path.startswith(base_path + "/")):
        path = path[len(base_path) :] or "/"
    if not path.startswith("/"):
        path = "/" + path
    if cur.query:
        path += "?" + cur.query
    return path


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def suggested_name(title: str) -> str:
    text = " ".join((title or "").split()).strip()
    if text and text.lower() not in ("about:blank",):
        return text[:80]
    return "录制 " + datetime.now().strftime("%Y-%m-%d %H:%M")


def recorder_init_script(base_url: str) -> str:
    return _RECORDER_JS.replace("__BASE_URL__", json.dumps(base_url))


@dataclass
class RecordSession:
    id: str
    project_id: int
    user_id: int
    base_url: str
    browser: str
    status: str = "starting"
    steps: list[dict[str, Any]] = field(default_factory=list)
    script: str = ""
    suggested_name: str = ""
    error_message: str = ""
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    stop_event: threading.Event = field(default_factory=threading.Event)
    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    _steps_lock: threading.Lock = field(default_factory=threading.Lock)

    def add_step(self, raw: Any) -> None:
        step = normalize_recorded_step(raw)
        if step is None:
            return
        with self._steps_lock:
            if self.steps and self.steps[-1] == step and step["action"] == "goto":
                return
            self.steps.append(step)

    def snapshot_steps(self) -> list[dict[str, Any]]:
        with self._steps_lock:
            return list(self.steps)

    def mark_ready(self) -> None:
        if self.status == "starting":
            self.status = "recording"
        self.ready.set()

    def fail(self, message: str) -> None:
        self.status = "failed"
        self.error_message = message
        self.ready.set()

    def finish(self) -> None:
        if self.status == "failed":
            return
        self.script = steps_to_script(self.snapshot_steps())
        if not self.suggested_name:
            self.suggested_name = suggested_name("")
        self.status = "stopped"

    def to_dict(self) -> dict[str, Any]:
        steps = self.snapshot_steps()
        return {
            "id": self.id,
            "status": self.status,
            "base_url": self.base_url,
            "step_count": len(steps),
            "script": self.script,
            "suggested_name": self.suggested_name,
            "error_message": self.error_message,
        }


def run_recording(session: RecordSession) -> None:
    _playwright_loop(session)


def _friendly_launch_error(ex: Exception) -> str:
    text = str(ex)
    if "Executable doesn't exist" in text or "playwright install" in text.lower():
        return "未安装 Playwright 浏览器。请在 backend 目录执行：uv run playwright install chromium"
    return f"{UI_RECORDING_FAILED}：{text}"


def _playwright_loop(session: RecordSession) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, session.browser, headless=False)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.expose_function("rainaRecordStep", lambda payload: session.add_step(payload))

            def _request_stop(*_args: Any) -> None:
                session.stop_event.set()

            context.expose_function("rainaStopRecording", _request_stop)
            context.add_init_script(recorder_init_script(session.base_url))
            page = context.new_page()
            try:
                page.goto(session.base_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as ex:  # noqa: BLE001
                log.warning("recorder initial goto failed: %s", ex)
            session.mark_ready()
            while not session.stop_event.is_set():
                try:
                    page.wait_for_timeout(300)
                except Exception:  # noqa: BLE001
                    break
            title = ""
            try:
                title = page.title() or ""
            except Exception:  # noqa: BLE001
                title = ""
            session.suggested_name = suggested_name(title)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def _resolve_target(db: Session, project_id: int, env_id: int | None) -> tuple[str, str]:
    env: UiEnvironment | None
    if env_id is not None:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.id == env_id, UiEnvironment.project_id == project_id)
            .first()
        )
        if env is None:
            raise AppError("not_found", UI_ENV_NOT_FOUND, status_code=404)
    else:
        env = (
            db.query(UiEnvironment)
            .filter(UiEnvironment.project_id == project_id, UiEnvironment.is_default.is_(True))
            .first()
        )
        if env is None:
            env = (
                db.query(UiEnvironment)
                .filter(UiEnvironment.project_id == project_id)
                .order_by(UiEnvironment.id)
                .first()
            )
    base = (env.base_url if env else "") or ""
    base = base.strip()
    if not base.startswith("http://") and not base.startswith("https://"):
        raise AppError("bad_request", UI_ENV_BASE_URL_REQUIRED, status_code=400)
    browser = ((env.browser if env else "") or "chromium").lower()
    if browser not in ("chromium", "firefox", "webkit"):
        browser = "chromium"
    return base, browser


def _prune_locked() -> None:
    if len(_sessions) <= _MAX_SESSIONS:
        return
    stopped = [s for s in _sessions.values() if s.status in ("stopped", "failed")]
    stopped.sort(key=lambda s: s.created_at)
    for row in stopped:
        if len(_sessions) <= _MAX_SESSIONS:
            break
        _sessions.pop(row.id, None)


def start_recording(
    db: Session,
    *,
    project_id: int,
    user_id: int,
    env_id: int | None,
) -> RecordSession:
    base_url, browser = _resolve_target(db, project_id, env_id)
    session = RecordSession(
        id=uuid.uuid4().hex,
        project_id=project_id,
        user_id=user_id,
        base_url=base_url,
        browser=browser,
    )
    with _lock:
        active = next((s for s in _sessions.values() if s.status in ("starting", "recording")), None)
        if active is not None:
            raise AppError("conflict", UI_RECORDING_BUSY, status_code=409)
        _sessions[session.id] = session
        _prune_locked()

    thread = threading.Thread(target=_thread_main, args=(session,), name=f"ui-rec-{session.id[:8]}", daemon=True)
    thread.start()
    if not session.ready.wait(LAUNCH_TIMEOUT_S):
        session.stop_event.set()
        session.fail("打开浏览器超时")
        session.done.wait(5)
        raise AppError("bad_request", UI_RECORDING_FAILED, detail="打开浏览器超时", status_code=400)
    if session.status == "failed":
        raise AppError("bad_request", session.error_message or UI_RECORDING_FAILED, status_code=400)
    return session


def _thread_main(session: RecordSession) -> None:
    try:
        run_recording(session)
    except Exception as ex:  # noqa: BLE001
        log.exception("ui recorder failed")
        session.fail(_friendly_launch_error(ex))
    else:
        session.finish()
    finally:
        session.ready.set()
        session.done.set()


def get_recording(project_id: int, session_id: str) -> RecordSession:
    with _lock:
        session = _sessions.get(session_id)
    if session is None or session.project_id != project_id:
        raise AppError("not_found", UI_RECORDING_NOT_FOUND, status_code=404)
    return session


def stop_recording(project_id: int, session_id: str) -> RecordSession:
    session = get_recording(project_id, session_id)
    if session.status in ("stopped", "failed"):
        return session
    session.stop_event.set()
    session.done.wait(STOP_TIMEOUT_S)
    if session.status in ("starting", "recording"):
        session.fail("停止录制超时")
    return session


def reset_sessions_for_tests() -> None:
    with _lock:
        for session in _sessions.values():
            session.stop_event.set()
        _sessions.clear()


_RECORDER_JS = r"""
(() => {
  if (window.top !== window) return;
  const baseUrl = __BASE_URL__;

  function cleanText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function safeName(value) {
    const text = cleanText(value);
    if (!text || text.length > 80 || /["\]]/.test(text)) return '';
    return text;
  }

  function stableId(el) {
    if (!el || !el.id || /\s/.test(el.id) || /^\d/.test(el.id)) return '';
    return el.id;
  }

  function testIdValue(el) {
    if (!el || el.nodeType !== 1) return '';
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'data-cy']) {
      const v = cleanText(el.getAttribute(attr));
      if (v) return v;
    }
    return '';
  }

  function resolveTarget(el) {
    let node = el;
    if (!node.matches('button, a, input, textarea, select, [role]')) {
      const host = node.closest('button, a, input, textarea, select, [role]');
      if (host) node = host;
    }
    if (node.tagName === 'LABEL') {
      const control = node.querySelector('input, textarea, select');
      if (control) return control;
    }
    return node;
  }

  function labelText(el) {
    const wrap = el.closest('label');
    if (wrap) {
      const clone = wrap.cloneNode(true);
      clone.querySelectorAll('input, textarea, select, button').forEach((n) => n.remove());
      const text = cleanText(clone.textContent);
      if (text) return text;
    }
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) {
        const text = cleanText(lab.textContent);
        if (text) return text;
      }
    }
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const text = cleanText(labelledby.split(/\s+/).map((id) => {
        const node = document.getElementById(id);
        return node ? node.textContent : '';
      }).join(' '));
      if (text) return text;
    }
    return '';
  }

  function implicitRole(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'text' || type === 'password' || type === 'email' || type === 'search' || type === 'tel' || type === 'url' || type === 'number') {
        return 'textbox';
      }
    }
    return '';
  }

  function isTextField(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'textarea' || tag === 'select') return true;
    if (tag !== 'input') return false;
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    return type !== 'button' && type !== 'submit' && type !== 'reset' && type !== 'checkbox' && type !== 'radio' && type !== 'file' && type !== 'hidden' && type !== 'image';
  }

  function visibleText(el) {
    return cleanText(el.innerText || el.textContent || '');
  }

  function cssPath(el) {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 4) {
      if (stableId(cur)) {
        parts.unshift('#' + CSS.escape(cur.id));
        break;
      }
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
      if (!cur || cur === document.body || cur === document.documentElement) break;
    }
    return parts.join(' > ');
  }

  function bestSelector(el) {
    if (!el || el.nodeType !== 1) return '';
    const target = resolveTarget(el);
    const testId = testIdValue(target);
    if (testId) return 'testid=' + testId;
    const id = stableId(target);
    if (id) return '#' + CSS.escape(id);
    if (isTextField(target)) {
      const lab = safeName(labelText(target));
      if (lab) return 'label=' + lab;
    }
    const role = implicitRole(target);
    const named = { button: 1, link: 1, checkbox: 1, radio: 1, option: 1, menuitem: 1, tab: 1, switch: 1, combobox: 1 };
    if (named[role]) {
      let name = '';
      if (role === 'checkbox' || role === 'radio' || role === 'combobox') {
        name = target.getAttribute('aria-label') || labelText(target);
      } else {
        name = target.getAttribute('aria-label') || visibleText(target);
      }
      name = safeName(name);
      if (name) return 'role=' + role + '[name="' + name + '"]';
    }
    if (role === 'textbox') {
      const aria = safeName(target.getAttribute('aria-label') || '');
      if (aria) return 'role=textbox[name="' + aria + '"]';
    }
    const placeholder = safeName(target.getAttribute('placeholder') || '');
    if (placeholder) return 'placeholder=' + placeholder;
    const nameAttr = target.getAttribute('name');
    if (nameAttr) return target.tagName.toLowerCase() + '[name="' + CSS.escape(nameAttr) + '"]';
    const aria = target.getAttribute('aria-label');
    if (aria) return '[aria-label="' + CSS.escape(aria) + '"]';
    return cssPath(target);
  }

  function toRelativePath(href) {
    try {
      const u = new URL(href, location.href);
      const b = new URL(baseUrl);
      if (u.origin !== b.origin) return null;
      let path = u.pathname || '/';
      const basePath = (b.pathname || '/').replace(/\/$/, '');
      if (basePath && basePath !== '/' && (path === basePath || path.startsWith(basePath + '/'))) {
        path = path.slice(basePath.length) || '/';
      }
      if (!path.startsWith('/')) path = '/' + path;
      if (u.search) path += u.search;
      return path;
    } catch (e) {
      return null;
    }
  }

  function append(step) {
    if (typeof window.rainaRecordStep === 'function') {
      try { window.rainaRecordStep(step); } catch (e) {}
    }
  }

  document.addEventListener('click', (e) => {
    const t = e.target;
    if (!t || !(t instanceof Element)) return;
    if (t.closest('[data-raina-recorder]')) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof window.rainaStopRecording === 'function') window.rainaStopRecording();
      return;
    }
    const selector = bestSelector(t);
    if (selector) append({ action: 'click', selector });
  }, true);

  document.addEventListener('change', (e) => {
    const t = e.target;
    if (!(t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement)) {
      return;
    }
    if (t.closest('[data-raina-recorder]')) return;
    const selector = bestSelector(t);
    if (!selector) return;
    if (t instanceof HTMLSelectElement) {
      append({ action: 'select_option', selector, value: t.value });
    } else if (t.type === 'checkbox' || t.type === 'radio') {
      append({ action: 'click', selector });
    } else {
      append({ action: 'fill', selector, value: t.value });
    }
  }, true);

  let lastPath = '';
  function maybeGoto() {
    const cur = location.pathname + location.search;
    if (cur === lastPath) return;
    lastPath = cur;
    const rel = toRelativePath(location.href);
    if (rel) append({ action: 'goto', value: rel });
  }
  maybeGoto();
  setInterval(maybeGoto, 500);

  function mountStop() {
    if (document.getElementById('raina-recorder-stop')) return;
    const bar = document.createElement('button');
    bar.id = 'raina-recorder-stop';
    bar.type = 'button';
    bar.setAttribute('data-raina-recorder', '1');
    bar.textContent = '停止录制';
    bar.style.cssText = [
      'position:fixed',
      'right:16px',
      'bottom:16px',
      'z-index:2147483647',
      'padding:8px 14px',
      'border:0',
      'border-radius:6px',
      'background:#1677ff',
      'color:#fff',
      'font:14px/1.2 system-ui,sans-serif',
      'cursor:pointer',
      'box-shadow:0 2px 8px rgba(0,0,0,.2)'
    ].join(';');
    const root = document.body || document.documentElement;
    if (root) root.appendChild(bar);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountStop);
  } else {
    mountStop();
  }
})();
"""
