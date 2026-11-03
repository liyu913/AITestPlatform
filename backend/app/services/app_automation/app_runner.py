"""APP step DSL runner using Appium (optional; injectable driver_factory for tests)."""

from __future__ import annotations

import re
import time
from typing import Any, Callable

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_ELEMENT_RE = re.compile(r"^@([A-Za-z0-9_.\-]+)$")

STRATEGY_ALIASES = {
    "id": "id",
    "accessibility_id": "accessibility id",
    "accessibility id": "accessibility id",
    "xpath": "xpath",
    "class_name": "class name",
    "class name": "class name",
    "android_uiautomator": "-android uiautomator",
    "-android uiautomator": "-android uiautomator",
    "ios_predicate": "-ios predicate string",
    "-ios predicate string": "-ios predicate string",
    "ios_class_chain": "-ios class chain",
    "-ios class chain": "-ios class chain",
}


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


def resolve_locator(
    step: dict[str, Any],
    *,
    elements: dict[str, dict[str, str]],
    ctx: dict[str, str],
) -> tuple[str | None, str | None]:
    """Return (strategy, selector) resolved from step or element library."""
    raw_el = (step.get("element") or "").strip()
    raw_sel = (step.get("selector") or "").strip()
    strategy = (step.get("strategy") or "").strip() or None
    key = raw_el
    if not key and raw_sel:
        m = _ELEMENT_RE.match(raw_sel)
        if m:
            key = m.group(1)
    if key:
        if key not in elements:
            lowered = {k.lower(): v for k, v in elements.items()}
            if key.lower() not in lowered:
                raise MissingElementError(f"元素库中不存在: {key}")
            meta = lowered[key.lower()]
        else:
            meta = elements[key]
        st = meta.get("strategy") or "id"
        sel = render_template(meta.get("selector") or "", ctx)
        return st, sel
    if raw_sel:
        return strategy or "id", render_template(raw_sel, ctx)
    return None, None


def _dump_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": step.get("action"),
        "strategy": step.get("strategy"),
        "selector": step.get("selector"),
        "element": step.get("element"),
        "value": step.get("value"),
        "assert": step.get("assert"),
        "timeout_ms": step.get("timeout_ms"),
        "retries": step.get("retries"),
    }


def _appium_by(strategy: str) -> str:
    key = (strategy or "id").strip().lower()
    return STRATEGY_ALIASES.get(key) or STRATEGY_ALIASES.get(strategy or "") or "id"


def _find(driver: Any, strategy: str | None, selector: str | None, timeout_ms: int) -> Any:
    if not selector:
        raise ValueError("缺少定位器")
    by = _appium_by(strategy or "id")
    # Injectable fake drivers (tests) expose find_element without a WebDriver session.
    if not hasattr(driver, "session_id"):
        return driver.find_element(by, selector)
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from appium.webdriver.common.appiumby import AppiumBy

    by_map = {
        "id": AppiumBy.ID,
        "accessibility id": AppiumBy.ACCESSIBILITY_ID,
        "xpath": AppiumBy.XPATH,
        "class name": AppiumBy.CLASS_NAME,
        "-android uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
        "-ios predicate string": AppiumBy.IOS_PREDICATE,
        "-ios class chain": AppiumBy.IOS_CLASS_CHAIN,
    }
    locator = (by_map.get(by, AppiumBy.ID), selector)
    wait = WebDriverWait(driver, max(timeout_ms, 100) / 1000.0)
    return wait.until(EC.presence_of_element_located(locator))


def _parse_swipe(value: str, size: dict[str, int]) -> tuple[int, int, int, int]:
    v = (value or "").strip().lower()
    w = int(size.get("width") or 1080)
    h = int(size.get("height") or 1920)
    mid_x, mid_y = w // 2, h // 2
    margin_x, margin_y = int(w * 0.2), int(h * 0.2)
    if v in ("left", "right", "up", "down"):
        if v == "left":
            return w - margin_x, mid_y, margin_x, mid_y
        if v == "right":
            return margin_x, mid_y, w - margin_x, mid_y
        if v == "up":
            return mid_x, h - margin_y, mid_x, margin_y
        return mid_x, margin_y, mid_x, h - margin_y
    parts = [p.strip() for p in v.split(",")]
    if len(parts) != 4:
        raise ValueError("swipe value 须为 left|right|up|down 或 x1,y1,x2,y2")
    return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])


def build_desired_caps(env: dict[str, Any]) -> dict[str, Any]:
    platform = (env.get("platform") or "android").lower()
    caps: dict[str, Any] = {
        "platformName": "Android" if platform == "android" else "iOS",
        "appium:deviceName": env.get("device_name") or "device",
        "appium:automationName": env.get("automation_name")
        or ("UiAutomator2" if platform == "android" else "XCUITest"),
        "appium:noReset": bool(env.get("no_reset", True)),
        "appium:fullReset": bool(env.get("full_reset", False)),
        "appium:newCommandTimeout": 120,
    }
    if env.get("udid"):
        caps["appium:udid"] = env["udid"]
    if env.get("platform_version"):
        caps["appium:platformVersion"] = env["platform_version"]
    if env.get("app_path"):
        caps["appium:app"] = env["app_path"]
    if platform == "android":
        if env.get("app_package"):
            caps["appium:appPackage"] = env["app_package"]
        if env.get("app_activity"):
            caps["appium:appActivity"] = env["app_activity"]
    else:
        if env.get("bundle_id"):
            caps["appium:bundleId"] = env["bundle_id"]
    extra = env.get("caps") if isinstance(env.get("caps"), dict) else {}
    caps.update(extra)
    return caps


def create_appium_driver(server_url: str, caps: dict[str, Any]) -> Any:
    from appium import webdriver
    from appium.options.common import AppiumOptions

    options = AppiumOptions()
    options.load_capabilities(caps)
    return webdriver.Remote(command_executor=server_url, options=options)


def execute_app_steps(
    *,
    env: dict[str, Any],
    steps: list[dict[str, Any]],
    ctx: dict[str, str],
    default_timeout_ms: int = 15000,
    default_retries: int = 1,
    elements: dict[str, dict[str, str]] | None = None,
    screenshot_saver: Callable[[bytes, str], str] | None = None,
    driver_factory: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    el_map = elements or {}
    driver: Any = None
    owns_driver = False
    try:
        if driver_factory is not None:
            driver = driver_factory()
        else:
            caps = build_desired_caps(env)
            driver = create_appium_driver(env.get("appium_server_url") or "http://127.0.0.1:4723", caps)
            owns_driver = True
        return _run_with_driver(
            driver=driver,
            env=env,
            steps=steps,
            ctx=ctx,
            elements=el_map,
            screenshot_saver=screenshot_saver,
            results=results,
            default_timeout_ms=default_timeout_ms,
            default_retries=default_retries,
        )
    finally:
        if owns_driver and driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass


def _execute_one_action(
    *,
    driver: Any,
    env: dict[str, Any],
    action: str,
    strategy: str | None,
    selector: str | None,
    val: str | None,
    asrt: str | None,
    timeout_ms: int,
    detail: dict[str, Any],
) -> None:
    if action == "launch":
        package = (val or env.get("app_package") or env.get("bundle_id") or "").strip()
        if hasattr(driver, "activate_app") and package:
            driver.activate_app(package)
            detail["app"] = package
        elif hasattr(driver, "launch_app"):
            driver.launch_app()
        else:
            detail["app"] = package or "noop"
    elif action == "tap":
        el = _find(driver, strategy, selector, timeout_ms)
        el.click()
    elif action == "type":
        el = _find(driver, strategy, selector, timeout_ms)
        el.clear()
        el.send_keys(val or "")
    elif action == "clear":
        el = _find(driver, strategy, selector, timeout_ms)
        el.clear()
    elif action == "long_press":
        el = _find(driver, strategy, selector, timeout_ms)
        if hasattr(driver, "execute_script"):
            try:
                driver.execute_script("mobile: longClickGesture", {"elementId": el.id})
            except Exception:  # noqa: BLE001
                el.click()
        else:
            el.click()
    elif action == "swipe":
        size = {"width": 1080, "height": 1920}
        if hasattr(driver, "get_window_size"):
            try:
                size = driver.get_window_size()
            except Exception:  # noqa: BLE001
                pass
        x1, y1, x2, y2 = _parse_swipe(val or "up", size)
        detail["swipe"] = [x1, y1, x2, y2]
        if hasattr(driver, "swipe"):
            driver.swipe(x1, y1, x2, y2, 600)
        elif hasattr(driver, "execute_script"):
            driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": min(x1, x2),
                    "top": min(y1, y2),
                    "width": abs(x2 - x1) or 10,
                    "height": abs(y2 - y1) or 10,
                    "direction": "up",
                    "percent": 0.75,
                },
            )
        else:
            detail["swipe_noop"] = True
    elif action == "back":
        if hasattr(driver, "back"):
            driver.back()
        elif hasattr(driver, "press_keycode"):
            driver.press_keycode(4)
    elif action == "hide_keyboard":
        if hasattr(driver, "hide_keyboard"):
            try:
                driver.hide_keyboard()
            except Exception:  # noqa: BLE001
                pass
    elif action == "wait":
        state = (asrt or val or "visible").strip()
        el = _find(driver, strategy, selector, timeout_ms)
        if state == "visible":
            if hasattr(el, "is_displayed") and not el.is_displayed():
                raise AssertionError("元素不可见")
        elif state in ("invisible", "gone"):
            if hasattr(el, "is_displayed") and el.is_displayed():
                raise AssertionError("元素仍可见")
    elif action == "assert_visible":
        el = _find(driver, strategy, selector, timeout_ms)
        if hasattr(el, "is_displayed") and not el.is_displayed():
            raise AssertionError("元素不可见")
    elif action == "assert_text":
        expected = asrt if asrt is not None else (val or "")
        el = _find(driver, strategy, selector, timeout_ms)
        text = ""
        if hasattr(el, "text"):
            text = el.text or ""
        if hasattr(el, "get_attribute"):
            text = text or (el.get_attribute("text") or "") or (el.get_attribute("content-desc") or "")
        detail["actual_text"] = text
        if expected not in text:
            raise AssertionError(f"文案不包含期望值: expected={expected!r} actual={text!r}")
    elif action == "screenshot":
        pass
    else:
        raise ValueError(f"未知动作: {action}")


def _run_with_driver(
    *,
    driver: Any,
    env: dict[str, Any],
    steps: list[dict[str, Any]],
    ctx: dict[str, str],
    elements: dict[str, dict[str, str]],
    screenshot_saver: Callable[[bytes, str], str] | None,
    results: list[dict[str, Any]],
    default_timeout_ms: int,
    default_retries: int,
) -> list[dict[str, Any]]:
    for idx, raw in enumerate(steps or []):
        step = raw if isinstance(raw, dict) else {}
        action = str(step.get("action") or "").strip()
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
            strategy, sel = resolve_locator(step, elements=elements, ctx=ctx)
            resolved_sel = sel or ""
            val = render_template(str(step.get("value")), ctx) if step.get("value") is not None else None
            asrt = (
                render_template(str(step.get("assert")), ctx) if step.get("assert") is not None else None
            )
            last_ex: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    _execute_one_action(
                        driver=driver,
                        env=env,
                        action=action,
                        strategy=strategy,
                        selector=sel,
                        val=val,
                        asrt=asrt,
                        timeout_ms=timeout_ms,
                        detail=detail,
                    )
                    if action == "screenshot" and screenshot_saver:
                        png = driver.get_screenshot_as_png()
                        screenshot_key = screenshot_saver(png, f"step_{idx}.png")
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
        except Exception as ex:  # noqa: BLE001
            status = "failed" if isinstance(ex, AssertionError) else "error"
            error_message = str(ex)
            detail["retries"] = retry_count
            if screenshot_saver:
                try:
                    png = driver.get_screenshot_as_png()
                    screenshot_key = screenshot_saver(png, f"fail_{idx}.png")
                except Exception:  # noqa: BLE001
                    pass

        duration_ms = int((time.perf_counter() - t0) * 1000)
        results.append(
            {
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
        )
        if status != "passed":
            break

    return results
