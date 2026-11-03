"""UI step DSL runner using Playwright sync API."""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from app.services.ui_automation.ui_url_guard import UnsafeUiUrlError, assert_same_host, join_base_and_path

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_ELEMENT_RE = re.compile(r"^@([A-Za-z0-9_.\-]+)$")
_GETBY_ROLE = re.compile(
    r"^(?:page\.)?get(?:ByRole|_by_role)\(\s*['\"](\w+)['\"]\s*(?:,\s*\{?\s*name\s*[:=]\s*(.+?)\s*\}?)?\s*\)\s*$",
    re.I | re.S,
)
_GETBY_ONE = re.compile(
    r"^(?:page\.)?get(?:By(Placeholder|Text|Label|TestId|Title|AltText)|_by_(placeholder|text|label|test_id|title|alt_text))"
    r"\(\s*(.+?)\s*\)\s*$",
    re.I | re.S,
)
# Lucide/Ant icon glyph names often leak into accessible names: "file-text 需求管理"
_ICON_KEBAB = re.compile(r"^([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+(.+)$")
_ICON_ASCII_CJK = re.compile(r"^([a-z][a-z0-9-]*)\s+([\u4e00-\u9fff].*)$")

SUPPORTED_BROWSERS = ("chromium", "firefox", "webkit")


def sanitize_accessible_name(name: str | Any) -> Any:
    """Drop leading icon tokens from explore/codegen accessible names.

    Playwright generate-locator may emit name=\"file-text 需求管理\" while the live
    accessible name is only \"需求管理\". Substring match on the cleaned label still
    works if the icon text is truly present.
    """
    if not isinstance(name, str):
        return name
    text = name.strip()
    if not text:
        return text
    m = _ICON_KEBAB.match(text)
    if m:
        rest = m.group(2).strip()
        return rest or text
    m2 = _ICON_ASCII_CJK.match(text)
    if m2:
        return m2.group(2).strip() or text
    return text


def rewrite_getby_name(expr: str) -> str:
    """Strip icon prefixes inside getByRole/getByLabel name strings."""

    def repl(m: re.Match[str]) -> str:
        prefix, quote, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        cleaned = sanitize_accessible_name(name)
        return f"{prefix}{quote}{cleaned}{quote}{suffix}"

    return re.sub(
        r"(getBy(?:Role|Label)\(\s*['\"]\w+['\"]\s*,\s*\{\s*name:\s*)(['\"])([^'\"]+)\2(\s*\})",
        repl,
        expr or "",
        count=1,
        flags=re.I,
    )


def _parse_name_arg(raw: str) -> Any:
    """Parse getBy* name/text arg: 'x' | \"x\" | /pat/ | /pat/i → str | re.Pattern."""
    text = (raw or "").strip().rstrip(",").strip()
    if not text:
        return ""
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return sanitize_accessible_name(text[1:-1])
    if text.startswith("/") and text.count("/") >= 2:
        end = text.rfind("/")
        if end > 0:
            flags = re.I if "i" in text[end + 1 :].lower() else 0
            return re.compile(text[1:end], flags)
    return sanitize_accessible_name(text.strip("\"'"))


def resolve_page_locator(root: Any, selector: str | None) -> Any:
    """Map explore/DSL selector strings to a Playwright locator.

    Never pass getByRole(...) into page.locator() — Playwright treats that as CSS
    and fails with: Unsupported token \"{\".
    """
    if not selector:
        raise ValueError("缺少 selector")
    s = selector.strip().rstrip(";").strip()
    m_role = _GETBY_ROLE.match(s)
    if m_role:
        role = m_role.group(1)
        name_raw = m_role.group(2)
        if name_raw:
            return root.get_by_role(role, name=_parse_name_arg(name_raw))
        return root.get_by_role(role)
    m_one = _GETBY_ONE.match(s)
    if m_one:
        kind = (m_one.group(1) or m_one.group(2) or "").lower().replace("_", "")
        arg = _parse_name_arg(m_one.group(3))
        if kind == "placeholder":
            return root.get_by_placeholder(arg)
        if kind == "text":
            return root.get_by_text(arg)
        if kind == "label":
            return root.get_by_label(arg)
        if kind == "testid":
            return root.get_by_test_id(arg if isinstance(arg, str) else str(arg))
        if kind == "title":
            return root.get_by_title(arg)
        if kind == "alttext":
            return root.get_by_alt_text(arg)
    if s.startswith("role="):
        m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", s)
        if m:
            role, name = m.group(1), m.group(2)
            if name and name.endswith("]"):
                name = name[:-1]
            name = sanitize_accessible_name((name or "").strip("\"'"))
            if name:
                return root.get_by_role(role, name=name)
            return root.get_by_role(role)
        return root.locator(s)
    if s.startswith("text="):
        return root.get_by_text(sanitize_accessible_name(s[5:].strip("\"'")), exact=False)
    if s.startswith("label="):
        return root.get_by_label(sanitize_accessible_name(s[6:].strip("\"'")))
    if s.startswith("placeholder="):
        return root.get_by_placeholder(s[len("placeholder=") :].strip("\"'"))
    if s.startswith("testid=") or s.startswith("data-testid="):
        tid = s.split("=", 1)[1].strip("\"'")
        return root.get_by_test_id(tid)
    return root.locator(s)


class MissingVariableError(ValueError):
    pass


class MissingElementError(ValueError):
    pass


def render_template(text: str, ctx: dict[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in ctx:
            raise MissingVariableError(f"缺少变量: {key}")
        return ctx[key]

    return _VAR_RE.sub(repl, text or "")


def resolve_selector(
    step: dict[str, Any],
    *,
    elements: dict[str, str],
    ctx: dict[str, str],
) -> str | None:
    """Resolve selector from step.selector, step.element, or @Page.name shorthand."""
    raw_el = (step.get("element") or "").strip()
    raw_sel = (step.get("selector") or "").strip()
    key = raw_el
    if not key and raw_sel:
        m = _ELEMENT_RE.match(raw_sel)
        if m:
            key = m.group(1)
    if key:
        # allow Page.name or page.name
        if key not in elements:
            # try case-insensitive page.name
            lowered = {k.lower(): v for k, v in elements.items()}
            if key.lower() not in lowered:
                raise MissingElementError(f"元素库中不存在: {key}")
            sel = lowered[key.lower()]
        else:
            sel = elements[key]
        return render_template(sel, ctx)
    if raw_sel:
        return render_template(raw_sel, ctx)
    return None


def _dump_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": step.get("action"),
        "selector": step.get("selector"),
        "element": step.get("element"),
        "value": step.get("value"),
        "assert": step.get("assert"),
        "timeout_ms": step.get("timeout_ms"),
        "retries": step.get("retries"),
    }


def _launch_browser(p: Any, browser_name: str, headless: bool) -> Any:
    name = (browser_name or "chromium").lower()
    if name not in SUPPORTED_BROWSERS:
        raise ValueError(f"不支持的浏览器: {browser_name}")
    launcher = getattr(p, name)
    return launcher.launch(headless=headless)


def execute_ui_steps(
    *,
    base_url: str,
    steps: list[dict[str, Any]],
    ctx: dict[str, str],
    viewport: dict[str, int] | None = None,
    headless: bool = True,
    browser_name: str = "chromium",
    default_timeout_ms: int = 15000,
    default_retries: int = 1,
    elements: dict[str, str] | None = None,
    screenshot_saver: Callable[[bytes, str], str] | None = None,
    browser_factory: Callable[..., Any] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    storage_state_path: str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
    screenshot_each_step: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    vp = viewport or {"width": 1280, "height": 720}
    width = int(vp.get("width") or 1280)
    height = int(vp.get("height") or 720)
    el_map = elements or {}

    if browser_factory is not None:
        return _run_with_page(
            page=browser_factory(),
            base_url=base_url,
            steps=steps,
            ctx=ctx,
            elements=el_map,
            screenshot_saver=screenshot_saver,
            results=results,
            default_timeout_ms=default_timeout_ms,
            default_retries=default_retries,
            on_step=on_step,
            screenshot_each_step=screenshot_each_step,
        )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch_browser(p, browser_name, headless)
        try:
            ctx_opts: dict[str, Any] = {
                "base_url": base_url,
                "viewport": {"width": width, "height": height},
            }
            if storage_state_path:
                ctx_opts["storage_state"] = storage_state_path
            if extra_http_headers:
                ctx_opts["extra_http_headers"] = dict(extra_http_headers)
            context = browser.new_context(**ctx_opts)
            if local_storage:
                from app.services.ui_automation.ui_env_auth import local_storage_init_script

                init_js = local_storage_init_script(local_storage)
                if init_js:
                    context.add_init_script(init_js)
            page = context.new_page()
            page.set_default_timeout(default_timeout_ms)
            return _run_with_page(
                page=page,
                base_url=base_url,
                steps=steps,
                ctx=ctx,
                elements=el_map,
                screenshot_saver=screenshot_saver,
                results=results,
                default_timeout_ms=default_timeout_ms,
                default_retries=default_retries,
                on_step=on_step,
                screenshot_each_step=screenshot_each_step,
            )
        finally:
            browser.close()


def _execute_one_action(
    *,
    page: Any,
    base_url: str,
    action: str,
    sel: str | None,
    val: str | None,
    asrt: str | None,
    timeout_ms: int,
    detail: dict[str, Any],
) -> None:
    if action == "goto":
        url = join_base_and_path(base_url, val or "/")
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        assert_same_host(base_url, page.url)
        detail["url"] = url
    elif action == "click":
        loc = resolve_page_locator(page, sel).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.click(timeout=timeout_ms)
    elif action == "fill":
        loc = resolve_page_locator(page, sel).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.fill(val or "", timeout=timeout_ms)
    elif action == "select_option":
        loc = resolve_page_locator(page, sel).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.select_option(val or "", timeout=timeout_ms)
    elif action == "press":
        loc = resolve_page_locator(page, sel).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.press(val or "Enter", timeout=timeout_ms)
    elif action == "hover":
        loc = resolve_page_locator(page, sel).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.hover(timeout=timeout_ms)
    elif action == "wait_for":
        # assert/value: visible|hidden|url|networkidle|load|domcontentloaded
        if asrt in ("visible", "hidden", "url", "networkidle", "load", "domcontentloaded"):
            state = asrt
        elif val in ("visible", "hidden", "url", "networkidle", "load", "domcontentloaded"):
            state = val
        else:
            state = "visible"
        if state == "url":
            expect = val or ""
            page.wait_for_url(f"**{expect}**", timeout=timeout_ms)
            assert_same_host(base_url, page.url)
        elif state in ("networkidle", "load", "domcontentloaded"):
            page.wait_for_load_state(state, timeout=timeout_ms)
        elif state == "hidden":
            resolve_page_locator(page, sel).first.wait_for(state="hidden", timeout=timeout_ms)
        else:
            resolve_page_locator(page, sel).first.wait_for(state="visible", timeout=timeout_ms)
    elif action == "assert_visible":
        resolve_page_locator(page, sel).first.wait_for(state="visible", timeout=timeout_ms)
    elif action == "assert_text":
        expected = asrt if asrt is not None else (val or "")
        text = resolve_page_locator(page, sel).first.inner_text(timeout=timeout_ms)
        detail["actual_text"] = text
        if expected not in text:
            raise AssertionError(f"文案不包含期望值: expected={expected!r} actual={text!r}")
    elif action == "assert_url":
        expected = asrt if asrt is not None else (val or "")
        cur = page.url
        detail["url"] = cur
        assert_same_host(base_url, cur)
        if expected not in cur:
            raise AssertionError(f"URL 不包含期望值: expected={expected!r} actual={cur!r}")
    elif action == "screenshot":
        pass  # handled by caller via screenshot_saver
    else:
        raise ValueError(f"未知动作: {action}")


def _capture_step_shot(
    page: Any,
    screenshot_saver: Callable[[bytes, str], str] | None,
    filename: str,
) -> str | None:
    if screenshot_saver is None or page is None:
        return None
    try:
        png = page.screenshot(full_page=False)
        return screenshot_saver(png, filename)
    except Exception:  # noqa: BLE001
        return None


def _run_with_page(
    *,
    page: Any,
    base_url: str,
    steps: list[dict[str, Any]],
    ctx: dict[str, str],
    elements: dict[str, str],
    screenshot_saver: Callable[[bytes, str], str] | None,
    results: list[dict[str, Any]],
    default_timeout_ms: int,
    default_retries: int,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    screenshot_each_step: bool = True,
) -> list[dict[str, Any]]:
    for idx, raw in enumerate(steps or []):
        step = raw if isinstance(raw, dict) else {}
        action = str(step.get("action") or "").strip()
        if not action or action == "done":
            continue
        timeout_ms = int(step.get("timeout_ms") or default_timeout_ms)
        retries = int(step.get("retries") if step.get("retries") is not None else default_retries)
        retries = max(0, min(retries, 5))
        t0 = time.perf_counter()
        detail: dict[str, Any] = {"step": _dump_step(step)}
        status = "passed"
        error_message = ""
        screenshot_key: str | None = None
        retry_count = 0
        resolved_sel = ""

        try:
            sel = resolve_selector(step, elements=elements, ctx=ctx)
            resolved_sel = sel or ""
            val = render_template(str(step.get("value")), ctx) if step.get("value") is not None else None
            asrt = (
                render_template(str(step.get("assert")), ctx) if step.get("assert") is not None else None
            )
            last_ex: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    _execute_one_action(
                        page=page,
                        base_url=base_url,
                        action=action,
                        sel=sel,
                        val=val,
                        asrt=asrt,
                        timeout_ms=timeout_ms,
                        detail=detail,
                    )
                    last_ex = None
                    retry_count = attempt
                    break
                except Exception as ex:  # noqa: BLE001
                    last_ex = ex
                    retry_count = attempt
                    if attempt < retries:
                        time.sleep(0.4 * (attempt + 1))
                        continue
            if last_ex is not None:
                raise last_ex
            if screenshot_saver and (screenshot_each_step or action == "screenshot"):
                screenshot_key = _capture_step_shot(page, screenshot_saver, f"step_{idx}.png")
        except Exception as ex:  # noqa: BLE001
            status = "failed" if isinstance(ex, AssertionError) else "error"
            error_message = str(ex)
            detail["retries"] = retry_count
            screenshot_key = _capture_step_shot(page, screenshot_saver, f"fail_{idx}.png")

        duration_ms = int((time.perf_counter() - t0) * 1000)
        row = {
            "order": idx,
            "action": action,
            "selector": resolved_sel or str(step.get("selector") or step.get("element") or ""),
            "status": status,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "screenshot_key": screenshot_key,
            "detail_json": detail,
            "retry_count": retry_count,
        }
        results.append(row)
        if on_step is not None:
            on_step(row)
        if status != "passed":
            break

    return results


def _guard_goto(page: Any, base_url: str) -> None:
    original = page.goto

    def guarded(url: Any, *args: Any, **kwargs: Any) -> Any:
        target = url
        if isinstance(url, str):
            path = url.strip()
            if path.startswith("/") and not path.startswith("//"):
                target = join_base_and_path(base_url, path)
        result = original(target, *args, **kwargs)
        current = getattr(page, "url", "") or ""
        if current and current != "about:blank":
            assert_same_host(base_url, current)
        return result

    page.goto = guarded


def _script_screenshot(
    page: Any,
    screenshot_saver: Callable[[bytes, str], str] | None,
    filename: str = "fail_script.png",
) -> str | None:
    if screenshot_saver is None or page is None:
        return None
    try:
        png = page.screenshot(full_page=False)
        return screenshot_saver(png, filename)
    except Exception:  # noqa: BLE001
        return None


_PAGE_STEP_ACTIONS = frozenset(
    {
        "goto",
        "click",
        "fill",
        "type",
        "press",
        "check",
        "uncheck",
        "select_option",
        "hover",
        "reload",
    }
)
_LOCATOR_STEP_ACTIONS = frozenset(
    {
        "click",
        "fill",
        "type",
        "press",
        "check",
        "uncheck",
        "select_option",
        "hover",
    }
)


class _ScriptPageInstrument:
    """Wrap Playwright page so each navigation/input action emits a step + screenshot."""

    def __init__(
        self,
        page: Any,
        *,
        screenshot_saver: Callable[[bytes, str], str] | None,
        on_step: Callable[[dict[str, Any]], None] | None,
        steps: list[dict[str, Any]],
    ) -> None:
        object.__setattr__(self, "_page", page)
        object.__setattr__(self, "_screenshot_saver", screenshot_saver)
        object.__setattr__(self, "_on_step", on_step)
        object.__setattr__(self, "_steps", steps)

    def __getattr__(self, name: str) -> Any:
        page = object.__getattribute__(self, "_page")
        attr = getattr(page, name)
        if name in _PAGE_STEP_ACTIONS and callable(attr):
            return self._wrap_action(name, attr, selector="")
        return attr

    def locator(self, *args: Any, **kwargs: Any) -> "_InstrumentedLocator":
        page = object.__getattribute__(self, "_page")
        loc = page.locator(*args, **kwargs)
        sel = str(args[0]) if args else str(kwargs.get("selector") or "")
        return _InstrumentedLocator(loc, root=self, selector=sel)

    def get_by_role(self, *args: Any, **kwargs: Any) -> "_InstrumentedLocator":
        page = object.__getattribute__(self, "_page")
        loc = page.get_by_role(*args, **kwargs)
        role = str(args[0]) if args else str(kwargs.get("role") or "role")
        name = kwargs.get("name")
        sel = f"role={role}" + (f" name={name}" if name is not None else "")
        return _InstrumentedLocator(loc, root=self, selector=sel)

    def get_by_text(self, *args: Any, **kwargs: Any) -> "_InstrumentedLocator":
        page = object.__getattribute__(self, "_page")
        loc = page.get_by_text(*args, **kwargs)
        text = str(args[0]) if args else str(kwargs.get("text") or "")
        return _InstrumentedLocator(loc, root=self, selector=f"text={text}")

    def get_by_label(self, *args: Any, **kwargs: Any) -> "_InstrumentedLocator":
        page = object.__getattribute__(self, "_page")
        loc = page.get_by_label(*args, **kwargs)
        text = str(args[0]) if args else str(kwargs.get("text") or "")
        return _InstrumentedLocator(loc, root=self, selector=f"label={text}")

    def get_by_placeholder(self, *args: Any, **kwargs: Any) -> "_InstrumentedLocator":
        page = object.__getattribute__(self, "_page")
        loc = page.get_by_placeholder(*args, **kwargs)
        text = str(args[0]) if args else str(kwargs.get("text") or "")
        return _InstrumentedLocator(loc, root=self, selector=f"placeholder={text}")

    def _wrap_action(self, action: str, fn: Callable[..., Any], *, selector: str) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            sel = selector
            if not sel and args and isinstance(args[0], str) and action in (
                "click",
                "fill",
                "type",
                "press",
                "check",
                "uncheck",
                "select_option",
                "hover",
            ):
                sel = args[0]
            if action == "goto" and args:
                sel = str(args[0])
            t0 = time.perf_counter()
            idx = len(self._steps)
            try:
                result = fn(*args, **kwargs)
            except Exception as ex:  # noqa: BLE001
                status = "failed" if isinstance(ex, AssertionError) else "error"
                shot = _script_screenshot(
                    self._page,
                    self._screenshot_saver,
                    f"fail_{idx}.png",
                )
                row = {
                    "order": idx,
                    "action": action,
                    "selector": sel or "",
                    "status": status,
                    "error_message": str(ex),
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "screenshot_key": shot,
                    "detail_json": {},
                    "retry_count": 0,
                }
                self._emit(row)
                raise
            shot = _script_screenshot(
                self._page,
                self._screenshot_saver,
                f"step_{idx}.png",
            )
            row = {
                "order": idx,
                "action": action,
                "selector": sel or "",
                "status": "passed",
                "error_message": "",
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "screenshot_key": shot,
                "detail_json": {},
                "retry_count": 0,
            }
            self._emit(row)
            return result

        return wrapped

    def _emit(self, row: dict[str, Any]) -> None:
        self._steps.append(row)
        if self._on_step is not None:
            self._on_step(row)


class _InstrumentedLocator:
    def __init__(self, loc: Any, *, root: _ScriptPageInstrument, selector: str) -> None:
        object.__setattr__(self, "_loc", loc)
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "_selector", selector)

    def __getattr__(self, name: str) -> Any:
        loc = object.__getattribute__(self, "_loc")
        root = object.__getattribute__(self, "_root")
        selector = object.__getattribute__(self, "_selector")
        attr = getattr(loc, name)
        if name in _LOCATOR_STEP_ACTIONS and callable(attr):
            return root._wrap_action(name, attr, selector=selector)
        return attr

    @property
    def first(self) -> "_InstrumentedLocator":
        loc = object.__getattribute__(self, "_loc")
        root = object.__getattribute__(self, "_root")
        selector = object.__getattribute__(self, "_selector")
        return _InstrumentedLocator(loc.first, root=root, selector=selector)

    @property
    def last(self) -> "_InstrumentedLocator":
        loc = object.__getattribute__(self, "_loc")
        root = object.__getattribute__(self, "_root")
        selector = object.__getattribute__(self, "_selector")
        return _InstrumentedLocator(loc.last, root=root, selector=selector)

    def nth(self, index: int) -> "_InstrumentedLocator":
        loc = object.__getattribute__(self, "_loc")
        root = object.__getattribute__(self, "_root")
        selector = object.__getattribute__(self, "_selector")
        return _InstrumentedLocator(loc.nth(index), root=root, selector=selector)


def execute_ui_script(
    *,
    base_url: str,
    script: str,
    ctx: dict[str, str],
    viewport: dict[str, int] | None = None,
    headless: bool = True,
    browser_name: str = "chromium",
    default_timeout_ms: int = 15000,
    screenshot_saver: Callable[[bytes, str], str] | None = None,
    browser_factory: Callable[..., Any] | None = None,
    storage_state_path: str | None = None,
    save_storage_state_path: str | None = None,
    extra_http_headers: dict[str, str] | None = None,
    local_storage: dict[str, str] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run a ``def run(page)`` Python script, or a Node ``async function run(page, agent)`` script."""
    from app.services.ui_automation.ui_node_script import is_node_ui_script

    if is_node_ui_script(script):
        from app.services.ui_automation.node_ui_runner import execute_node_ui_script

        return execute_node_ui_script(
            base_url=base_url,
            script=script,
            ctx=ctx,
            viewport=viewport,
            headless=headless,
            browser_name=browser_name,
            default_timeout_ms=default_timeout_ms,
            storage_state_path=storage_state_path,
            save_storage_state_path=save_storage_state_path,
            extra_http_headers=extra_http_headers,
            local_storage=local_storage,
            screenshot_saver=screenshot_saver,
            on_log=on_log,
            on_step=on_step,
        )
    t0 = time.perf_counter()
    status = "passed"
    error_message = ""
    page_box: dict[str, Any] = {"page": None}
    step_rows: list[dict[str, Any]] = []

    def _invoke(raw_page: Any) -> None:
        page_box["page"] = raw_page
        if hasattr(raw_page, "set_default_timeout"):
            raw_page.set_default_timeout(default_timeout_ms)
        _guard_goto(raw_page, base_url)
        from playwright.sync_api import expect as pw_expect

        instrumented = _ScriptPageInstrument(
            raw_page,
            screenshot_saver=screenshot_saver,
            on_step=on_step,
            steps=step_rows,
        )
        namespace: dict[str, Any] = {
            "__builtins__": __builtins__,
            "expect": pw_expect,
            "env": {str(k): str(v) for k, v in (ctx or {}).items()},
            "re": re,
        }
        compiled = compile(script or "", "<ui-script>", "exec")
        exec(compiled, namespace)  # noqa: S102
        run = namespace.get("run")
        if not callable(run):
            raise ValueError("脚本需要定义 def run(page)")
        run(instrumented)
        current = getattr(raw_page, "url", "") or ""
        if current and current != "about:blank":
            assert_same_host(base_url, current)

    try:
        if not (script or "").strip():
            raise ValueError("用例没有可执行脚本")
        if browser_factory is not None:
            page = browser_factory()
            _invoke(page)
        else:
            from playwright.sync_api import sync_playwright

            vp = viewport or {"width": 1280, "height": 720}
            width = int(vp.get("width") or 1280)
            height = int(vp.get("height") or 720)
            with sync_playwright() as p:
                browser = _launch_browser(p, browser_name, headless)
                try:
                    ctx_opts: dict[str, Any] = {
                        "base_url": base_url,
                        "viewport": {"width": width, "height": height},
                    }
                    if storage_state_path:
                        ctx_opts["storage_state"] = storage_state_path
                    if extra_http_headers:
                        ctx_opts["extra_http_headers"] = dict(extra_http_headers)
                    context = browser.new_context(**ctx_opts)
                    if local_storage:
                        from app.services.ui_automation.ui_env_auth import local_storage_init_script

                        init_js = local_storage_init_script(local_storage)
                        if init_js:
                            context.add_init_script(init_js)
                    page = context.new_page()
                    try:
                        _invoke(page)
                        if save_storage_state_path:
                            context.storage_state(path=save_storage_state_path)
                    except Exception:
                        raise
                finally:
                    browser.close()
    except AssertionError as ex:
        status = "failed"
        error_message = str(ex)
    except Exception as ex:  # noqa: BLE001
        status = "error"
        error_message = str(ex)

    if step_rows:
        if status != "passed" and (
            not step_rows or str(step_rows[-1].get("status") or "") == "passed"
        ):
            # Failure outside instrumented actions (e.g. expect) — append summary row.
            shot = _script_screenshot(
                page_box.get("page"),
                screenshot_saver,
                "fail_script.png",
            )
            row = {
                "order": len(step_rows),
                "action": "script",
                "selector": "",
                "status": status,
                "error_message": error_message,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "screenshot_key": shot,
                "detail_json": {},
                "retry_count": 0,
            }
            step_rows.append(row)
            if on_step is not None:
                on_step(row)
        return step_rows

    # No page actions — keep a single summary row with final screenshot.
    shot_name = "ok_script.png" if status == "passed" else "fail_script.png"
    screenshot_key = _script_screenshot(page_box.get("page"), screenshot_saver, shot_name)
    row = {
        "order": 0,
        "action": "script",
        "selector": "",
        "status": status,
        "error_message": error_message,
        "duration_ms": int((time.perf_counter() - t0) * 1000),
        "screenshot_key": screenshot_key,
        "detail_json": {},
        "retry_count": 0,
    }
    if on_step is not None:
        on_step(row)
    return [row]
