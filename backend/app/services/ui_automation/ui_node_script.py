"""Compile UI explore actions into a Node Playwright + Midscene script."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from app.services.ui_automation.playwright_cli_session import is_snapshot_ref_locator

_CAPTCHA = re.compile(r"验证码|captcha|verify[\s_-]?code|vcode", re.I)
_USER_FIELD = re.compile(r"账号|用户名|username|user\s*name", re.I)
_PASS_FIELD = re.compile(r"密码|password|passwd", re.I)
_CAPTCHA_QUERY_PROMPT = (
    "只读取验证码图片中的字符；忽略输入框已有文字与背景噪点；"
    "只输出字符本身，不要引号空格或解释"
)


def is_node_ui_script(script: str) -> bool:
    return "async function run" in (script or "")


def is_runnable_ui_script(script: str) -> bool:
    text = script or ""
    return "def run" in text or "async function run" in text


def _js(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _is_captcha(*parts: str) -> bool:
    return any(_CAPTCHA.search(p or "") for p in parts)


def _path_of(url: str) -> str:
    text = (url or "").strip() or "/"
    if text.startswith("/"):
        return text
    parsed = urlparse(text)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path if path.startswith("/") else "/" + path


def _label(text: str, pattern: re.Pattern[str], fallback: str) -> str:
    if pattern.search(text or ""):
        found = pattern.search(text or "")
        if found:
            return found.group(0)
    return fallback


def _script_ctx(
    ctx: dict[str, str] | None = None,
    *,
    username: str = "",
    password: str = "",
) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (ctx or {}).items():
        key = str(k).strip()
        val = str(v or "").strip()
        if key and val:
            out[key] = val
    if str(username or "").strip():
        out.setdefault("username", str(username).strip())
    if str(password or "").strip():
        out.setdefault("password", str(password).strip())
    return out


def _env_ref(key: str) -> str:
    k = str(key or "").strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
        return f"env.{k}"
    return f"env[{_js(k)}]"


def _value_expr(
    value: str,
    *,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
    field_hint: str = "",
) -> str:
    """Map fill values to env.<key> when they match provided ctx; else JSON literal.

    Field-name semantics win over a wrong recorded value when the matching ctx key exists
    (e.g. textbox named 账号 with value mistyped as the password → still env.username).
    """
    env = _script_ctx(ctx, username=username, password=password)
    hint = field_hint or ""
    hint_l = hint.lower()
    text = str(value or "")
    # Template {{key}}
    m = re.search(r"\{\{\s*([^\s}]+)\s*\}\}", text)
    if m:
        want = m.group(1).strip()
        for k in env:
            if k == want or k.lower() == want.lower():
                return _env_ref(k)
    # Convention: when ctx provides username/password, map a11y login fields to those keys
    # (only if those keys exist — not a decide/bind hardcode).
    if hint and not _is_captcha(hint):
        if "username" in env and _USER_FIELD.search(hint):
            return _env_ref("username")
        if "password" in env and _PASS_FIELD.search(hint):
            return _env_ref("password")
    # Hint contains key / key part (camelCase split)
    ranked = sorted(env.keys(), key=lambda k: (-len(str(k)), str(k).lower()))
    if hint_l and not _is_captcha(hint_l):
        for k in ranked:
            parts = [
                k.lower(),
                *[
                    p.lower()
                    for p in re.split(
                        r"[_\-\s]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", k)
                    )
                    if p
                ],
            ]
            if any(len(p) >= 2 and p in hint_l for p in parts):
                return _env_ref(k)
    # Exact value match
    for k in ranked:
        if text and text == env[k]:
            return _env_ref(k)
    return _js(value)


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            dumped = obj.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not str(k).startswith("_")}
    return {}


_GETBY_PREFIX = re.compile(
    r"^(getByRole|getByPlaceholder|getByText|getByLabel|getByTestId|getByTitle|getByAltText)\s*\(",
    re.I,
)


def _page_locator_expr(raw: str) -> str:
    """Turn explore selector / cli_locator into a JS page.* locator expression (no await).

    Never wrap getBy* strings in page.locator(\"getBy...\") — that makes Playwright
    parse them as CSS and fail on `{`.
    """
    from app.services.ui_automation.ui_runner import rewrite_getby_name, sanitize_accessible_name

    s = (raw or "").strip().rstrip(";").strip()
    if not s:
        return ""
    if is_snapshot_ref_locator(s):
        return ""
    if s.startswith("page."):
        return rewrite_getby_name(s)
    if _GETBY_PREFIX.match(s):
        return rewrite_getby_name(f"page.{s}")
    if s.startswith("placeholder="):
        ph = s.split("=", 1)[1].strip().strip("\"'")
        return f"page.getByPlaceholder({_js(ph)})"
    if s.startswith("label="):
        lab = sanitize_accessible_name(s.split("=", 1)[1].strip().strip("\"'"))
        return f"page.getByLabel({_js(lab)})"
    if s.startswith("text="):
        t = sanitize_accessible_name(s[5:].strip().strip("\"'"))
        return f"page.getByText({_js(t)})"
    if s.startswith("role="):
        m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", s)
        if m:
            role, name = m.group(1), m.group(2)
            if name and name.endswith("]"):
                name = name[:-1]
            name = sanitize_accessible_name((name or "").strip("\"'"))
            if name:
                return f"page.getByRole({_js(role)}, {{ name: {_js(name)} }})"
            return f"page.getByRole({_js(role)})"
    if s.startswith("testid=") or s.startswith("data-testid="):
        tid = s.split("=", 1)[1].strip().strip("\"'")
        return f"page.getByTestId({_js(tid)})"
    if is_snapshot_ref_locator(s):
        return ""
    return f"page.locator({_js(s)})"


def _captcha_fill_locator(action: dict[str, Any]) -> str:
    """Playwright locator for the captcha *input* (never the image). No await."""
    placeholder = str(action.get("placeholder") or "").strip()
    name = str(action.get("name") or "").strip()
    role = str(action.get("role") or "").strip()
    selector = str(action.get("selector") or "").strip()
    cli_loc = str(action.get("cli_locator") or "").strip()
    # Prefer explore-time generate-locator (never snapshot refs)
    if cli_loc and not is_snapshot_ref_locator(cli_loc) and (
        cli_loc.startswith("page.") or _GETBY_PREFIX.match(cli_loc)
    ):
        expr = _page_locator_expr(cli_loc)
        if expr:
            return expr
    if selector and not is_snapshot_ref_locator(selector) and (
        selector.startswith("page.") or _GETBY_PREFIX.match(selector)
    ):
        expr = _page_locator_expr(selector)
        if expr:
            return expr
    # Label text mis-recorded as placeholder (e.g. 「验证码」) — use role/name instead
    if placeholder and _CAPTCHA.search(placeholder) and len(placeholder) <= 8:
        placeholder = ""
    if placeholder:
        return f"page.getByPlaceholder({_js(placeholder)})"
    if role and name:
        return f"page.getByRole({_js(role)}, {{ name: {_js(name)} }})"
    if name:
        return f"page.getByRole('textbox', {{ name: {_js(name)} }})"
    if selector.startswith("placeholder="):
        ph = selector.split("=", 1)[1].strip().strip("\"'")
        if ph and not (_CAPTCHA.search(ph) and len(ph) <= 8):
            return f"page.getByPlaceholder({_js(ph)})"
    if selector.startswith("label="):
        lab = selector.split("=", 1)[1].strip().strip("\"'")
        if lab:
            return f"page.getByLabel({_js(lab)})"
    if selector.startswith("role="):
        expr = _page_locator_expr(selector)
        if expr:
            return expr
    if selector and not is_snapshot_ref_locator(selector):
        expr = _page_locator_expr(selector)
        if expr:
            return expr
    locate = str(action.get("locate") or action.get("target") or "").strip()
    if locate and not locate.startswith("page."):
        # Soft fallback: accessible name from explore locate text
        short = locate[:40]
        return f"page.getByRole('textbox', {{ name: {_js(short)} }})"
    raise ValueError("captcha fill missing locator from explore (cli_locator/name/placeholder)")


def _captcha_fill_statements(action: dict[str, Any]) -> list[str]:
    """Midscene aiQuery reads the image; Playwright fills the input. Never hardcode OCR chars."""
    try:
        locator = _captcha_fill_locator(action)
    except ValueError:
        # Last resort: let Midscene locate the input by natural language from explore
        locate = str(action.get("locate") or action.get("prompt") or "验证码输入框").strip()
        return [
            "{",
            (
                f"  const captcha = String(await agent.aiQuery({_js(_CAPTCHA_QUERY_PROMPT)}))"
                ".replace(/[^a-zA-Z0-9]/g, '');"
            ),
            f"  await agent.aiInput(captcha, {_js(locate)});",
            "}",
        ]
    return [
        "{",
        (
            f"  const captcha = String(await agent.aiQuery({_js(_CAPTCHA_QUERY_PROMPT)}))"
            ".replace(/[^a-zA-Z0-9]/g, '');"
        ),
        f"  await {locator}.fill(captcha);",
        "}",
    ]


def _explore_page_locator(action: dict[str, Any]) -> str:
    """Best stable page.* locator from explore fields (testid > role > …). Empty if none."""
    from app.services.ui_automation.ui_locator_stability import (
        extract_step_locators,
        rank_locator_exprs,
    )

    ranked = extract_step_locators(action)
    if ranked:
        # Prefer converted exprs that also survive _page_locator_expr sanitization
        converted: list[str] = []
        for expr in ranked:
            base = _page_locator_expr(expr) or expr
            if base and not is_snapshot_ref_locator(base):
                converted.append(base)
        ordered = rank_locator_exprs(converted)
        return ordered[0] if ordered else ""
    return ""


def _fill_statement(
    action: dict[str, Any],
    *,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
) -> str | list[str]:
    locate = str(action.get("locate") or "")
    placeholder = str(action.get("placeholder") or "")
    value = str(action.get("value") or "")
    blob = " ".join(
        [
            locate,
            placeholder,
            str(action.get("name") or ""),
            str(action.get("selector") or ""),
            str(action.get("cli_locator") or ""),
            str(action.get("target") or ""),
        ]
    )
    if action.get("kind") == "fill_captcha" or _is_captcha(blob):
        return _captcha_fill_statements(action)
    # Username/password heuristics only affect the *value* (env.*), never the locator.
    expr = _value_expr(value, ctx=ctx, username=username, password=password, field_hint=blob)
    if str(action.get("driver") or "").lower() == "midscene":
        return f"await agent.aiInput({expr}, {_js(locate or '输入框')});"

    explore_loc = _explore_page_locator(action)
    if explore_loc:
        if re.search(r"\.(fill|click)\s*\(", explore_loc):
            return f"await {explore_loc};"
        return f"await {explore_loc}.fill({expr});"

    # No explore locator — soft heuristics (last resort only).
    if placeholder and not _USER_FIELD.search(placeholder) and not _PASS_FIELD.search(placeholder):
        return f"await page.getByPlaceholder({_js(placeholder)}).fill({expr});"
    if _USER_FIELD.search(blob):
        name = _label(blob, _USER_FIELD, "账号")
        return f"await page.getByRole('textbox', {{ name: {_js(name)} }}).fill({expr});"
    if _PASS_FIELD.search(blob):
        name = _label(blob, _PASS_FIELD, "密码")
        return f"await page.getByRole('textbox', {{ name: {_js(name)} }}).fill({expr});"
    if locate:
        return f"await agent.aiInput({expr}, {_js(locate)});"
    return f"await agent.aiInput({expr}, '输入框');"


def _click_statement(action: dict[str, Any]) -> str:
    role = str(action.get("role") or "").strip()
    name = str(action.get("name") or "").strip()
    locate = str(action.get("locate") or "").strip()
    selector = str(action.get("selector") or "").strip()
    if str(action.get("driver") or "").lower() == "midscene":
        return f"await agent.aiTap({_js(locate or name or '按钮')});"

    # Prefer explore-grounded locator (cli_locator / role+name / …) by stability.
    explore_loc = _explore_page_locator(action)
    if explore_loc:
        if re.search(r"\.(fill|click)\s*\(", explore_loc):
            return f"await {explore_loc};"
        return f"await {explore_loc}.click();"

    # No explore locator — derive from role/name/locate only as fallback.
    if not name and locate:
        if len(locate) <= 20 and not locate.startswith("page.") and "=" not in locate:
            cleaned = re.sub(r"(按钮|button|控件)$", "", locate, flags=re.I).strip() or locate
            name = cleaned
            role = role or "button"
    if not name and selector.startswith("role="):
        m = re.match(r"role=([^\s\[]+)(?:\[name=(.+)\])?", selector)
        if m:
            role = role or m.group(1)
            raw_name = m.group(2)
            if raw_name:
                if raw_name.endswith("]"):
                    raw_name = raw_name[:-1]
                name = raw_name.strip("\"'")
    if role and name:
        from app.services.ui_automation.ui_runner import sanitize_accessible_name

        return (
            f"await page.getByRole({_js(role)}, "
            f"{{ name: {_js(sanitize_accessible_name(name))} }}).click();"
        )
    placeholder = str(action.get("placeholder") or "").strip()
    if placeholder:
        return f"await page.getByPlaceholder({_js(placeholder)}).click();"
    if selector.startswith("text="):
        base = _page_locator_expr(selector)
        if base:
            return f"await {base}.click();"
    if selector and not is_snapshot_ref_locator(selector):
        base = _page_locator_expr(selector)
        if base:
            return f"await {base}.click();"
    if locate:
        return f"await agent.aiTap({_js(locate)});"
    return 'await agent.aiTap("按钮");'


def _js_regex_literal(pattern: str) -> str:
    """Build a JS RegExp source string (JSON-quoted) for Playwright toHaveURL."""
    escaped = re.sub(r"([.\\+*?()[\]{}|^$])", r"\\\1", pattern or "")
    return _js(escaped)


def _leave_start_path_asserts(start_path: str) -> list[str]:
    """Assert URL left the case start_path (no hardcoded /login|/signin)."""
    path = _path_of(start_path or "/")
    if not path or path == "/":
        # No auth entry path — only wait until pathname is more than bare root.
        return [
            "await page.waitForURL((url) => (url.pathname || '/') !== '/', { timeout: 15000 });",
        ]
    # Escape path for RegExp source; keep ^ $ as anchors (do not run through _js_regex_literal).
    body = re.sub(r"([.\\+*?()[\]{}|^$])", r"\\\1", path.rstrip("/"))
    pat = f"^{body}/?$"
    lit = _js(pat)
    return [
        f"await page.waitForURL((url) => !new RegExp({lit}, 'i').test(url.pathname), {{ timeout: 15000 }});",
        f"await expect(page).not.toHaveURL(new RegExp({lit}, 'i'));",
    ]


def _assert_statements(
    step: dict[str, Any],
    *,
    start_path: str = "/",
) -> list[str]:
    action = str(step.get("action") or "").strip()
    value = str(step.get("value") or step.get("target") or "").strip()
    if action == "assert_url":
        if value.startswith("/") or value.startswith("http"):
            return [f"await expect(page).toHaveURL(new RegExp({_js_regex_literal(value)}));"]
        m = re.search(r"(/[^\s，,；;]+)", value)
        path = m.group(1) if m else ""
        if path.startswith("/"):
            return [f"await expect(page).toHaveURL(new RegExp({_js_regex_literal(path)}));"]
        # Vague leave-auth / success copy → leave case start_path (from explore/matrix).
        return _leave_start_path_asserts(start_path)
    if action in ("assert_text", "assert_visible"):
        text = value or str(step.get("target") or "")
        if not text:
            return []
        needle = text
        if len(needle) > 40 and ("完成" in needle or "通过" in needle or "成功" in needle):
            for token in ("成功", "首页", "错误", "必填", "完成", "通过"):
                if token in needle:
                    needle = token
                    break
            else:
                needle = needle[:40]
        return [f"await expect(page.getByText({_js(needle)}).first()).toBeVisible();"]
    return []


def _submit_locate_from_actions(actions: list[dict[str, Any]] | None) -> str:
    """Last click/tap locate/name from actions — no hardcoded submit label."""
    for raw in reversed(actions or []):
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or raw.get("action") or "").strip().lower()
        if kind not in ("click", "tap", "ai_tap"):
            continue
        locate = str(
            raw.get("locate") or raw.get("name") or raw.get("prompt") or raw.get("selector") or ""
        ).strip()
        if locate and not is_snapshot_ref_locator(locate):
            return locate
        if locate:
            # Prefer name alongside ref
            name = str(raw.get("name") or "").strip()
            if name:
                return name
    return ""


def script_has_assert(script: str) -> bool:
    text = script or ""
    return "expect(page" in text or "toHaveURL" in text or "toBeVisible" in text


def matrix_row_to_node_script(
    row: dict[str, Any],
    *,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
    start_path: str = "/",
) -> str:
    """Compile one matrix row into an independent ``async function run(page, agent)``."""
    return matrix_to_node_script(
        [row],
        ctx=ctx,
        username=username,
        password=password,
        start_path=start_path or str(row.get("start_url") or "/"),
        only_ready=False,
    )


def _is_login_case_name(name: str) -> bool:
    return bool(re.search(r"登录|login|sign\s*in", name or "", re.I))


def matrix_to_case_scripts(
    matrix: list[dict[str, Any]] | None,
    *,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
    start_path: str = "/",
) -> list[dict[str, Any]]:
    """Build per-case scripts after explore. Unready rows get empty code."""
    cases: list[dict[str, Any]] = []
    for idx, row in enumerate(matrix or []):
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip() or "UC"
        name = str(row.get("name") or "").strip() or cid
        ready = bool(row.get("ready"))
        needs_auth = bool(row.get("needs_auth")) or (
            idx > 0 and not _is_login_case_name(name)
        )
        code = ""
        if ready:
            code = matrix_row_to_node_script(
                row,
                ctx=ctx,
                username=username,
                password=password,
                start_path=start_path or str(row.get("start_url") or "/"),
            )
            if needs_auth and code and "// auth:" not in code:
                code = (
                    "// auth: reuse storageState from login case (dry-run loads saved auth)\n"
                    + code
                )
            if not script_has_assert(code):
                # Still emit code; dry-run gate will fail if no assert
                pass
        cases.append(
            {
                "id": cid,
                "name": name,
                "ready": ready,
                "code": code,
                "start_url": str(row.get("start_url") or start_path or "/"),
                "needs_auth": needs_auth and not _is_login_case_name(name),
                "is_login": _is_login_case_name(name),
            }
        )
    return cases


def matrix_to_node_script(
    matrix: list[dict[str, Any]] | None,
    *,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
    start_path: str = "/",
    only_ready: bool = True,
) -> str:
    """Compile structured matrix rows into ``async function run(page, agent)`` with asserts."""
    lines: list[str] = []
    saw_goto = False
    for row in matrix or []:
        if not isinstance(row, dict):
            continue
        if only_ready and row.get("ready") is False:
            continue
        cid = str(row.get("id") or "").strip() or "UC"
        name = str(row.get("name") or "").strip()
        lines.append(f"// --- {cid}: {name} ---")
        for step in row.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if only_ready and not step.get("ready") and not str(step.get("action") or "").startswith("assert"):
                continue
            action = str(step.get("action") or "").strip()
            if action == "goto":
                path = _path_of(str(step.get("value") or step.get("target") or row.get("start_url") or start_path))
                lines.append(f"await page.goto({_js(path)});")
                saw_goto = True
            elif action in ("fill", "fill_captcha", "input"):
                payload = dict(step)
                if step.get("captcha") or _is_captcha(
                    str(step.get("target") or ""),
                    str(step.get("locate") or ""),
                    str(step.get("placeholder") or ""),
                ):
                    payload["kind"] = "fill_captcha"
                else:
                    payload["kind"] = "fill"
                # Prefer target as locate when midscene / missing placeholder
                if not payload.get("locate") and step.get("target"):
                    payload["locate"] = step["target"]
                if not payload.get("placeholder") and str(step.get("target") or "").startswith("placeholder="):
                    payload["placeholder"] = str(step["target"]).split("=", 1)[1]
                stmt = _fill_statement(payload, ctx=ctx, username=username, password=password)
                if isinstance(stmt, list):
                    lines.extend(stmt)
                else:
                    lines.append(stmt)
            elif action in ("click", "tap"):
                payload = dict(step)
                payload["kind"] = "click"
                if not payload.get("locate") and step.get("target"):
                    payload["locate"] = step["target"]
                # Only invent role=button from bare Chinese when explore left no locator fields.
                has_explore = bool(
                    str(payload.get("cli_locator") or "").strip()
                    or str(payload.get("selector") or "").strip()
                    or str(payload.get("role") or "").strip()
                    or str(payload.get("name") or "").strip()
                )
                if (
                    not has_explore
                    and not payload.get("name")
                    and step.get("target")
                ):
                    tgt = str(step.get("target") or "")
                    if not tgt.startswith("page.") and "=" not in tgt:
                        payload.setdefault("name", tgt)
                        payload.setdefault("role", "button")
                lines.append(_click_statement(payload))
            elif action == "press":
                key = str(step.get("value") or "Enter")
                lines.append(f"await page.keyboard.press({_js(key)});")
            elif action.startswith("assert"):
                row_start = str(row.get("start_url") or start_path or "/")
                lines.extend(_assert_statements(step, start_path=row_start))
    if not saw_goto and lines:
        lines.insert(0, f"await page.goto({_js(_path_of(start_path))});")
    if not lines:
        return ""
    # ``expect`` is injected by ui-runner/run.mjs into the AsyncFunction scope.
    body = "\n".join(f"  {line}" for line in lines)
    return f"async function run(page, agent) {{\n{body}\n}}\n"


def actions_to_node_script(
    actions: list[dict[str, Any]],
    *,
    ctx: dict[str, str] | None = None,
    username: str = "",
    password: str = "",
    start_path: str = "/",
    ensure_submit: bool = False,
) -> str:
    lines: list[str] = []
    saw_goto = False
    saw_click = False
    for raw in actions or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or raw.get("action") or "").strip()
        if kind == "goto":
            path = _path_of(str(raw.get("path") or raw.get("value") or start_path))
            lines.append(f"await page.goto({_js(path)});")
            saw_goto = True
        elif kind in ("fill", "fill_captcha", "input"):
            stmt = _fill_statement(raw, ctx=ctx, username=username, password=password)
            if isinstance(stmt, list):
                lines.extend(stmt)
            else:
                lines.append(stmt)
        elif kind in ("click", "tap"):
            lines.append(_click_statement(raw))
            saw_click = True
        elif kind == "press":
            key = str(raw.get("value") or "Enter")
            locate = str(raw.get("locate") or "")
            if locate:
                lines.append(f"await agent.aiKeyboardPress({_js(locate)}, {_js(key)});")
    if not saw_goto:
        lines.insert(0, f"await page.goto({_js(_path_of(start_path))});")
    # Only append submit when explore already recorded a click locate — never hardcode 「提交」.
    if ensure_submit and not saw_click:
        locate = _submit_locate_from_actions(actions)
        if locate:
            lines.append(f"await agent.aiTap({_js(locate)});")
    if not lines:
        return ""
    body = "\n".join(f"  {line}" for line in lines)
    return f"async function run(page, agent) {{\n{body}\n}}\n"


def _locate_text(value: Any) -> str:
    """Extract readable locate/prompt text; never str(object) → '[object Object]'."""
    if value is None or value is False:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if not isinstance(value, dict):
        return ""
    display = value.get("promptDisplay")
    if isinstance(display, str) and display.strip():
        return display.strip()
    prompt = value.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    if isinstance(prompt, dict):
        nested = _locate_text(prompt)
        if nested:
            return nested
    for key in ("description", "text"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if "locate" in value:
        nested = _locate_text(value.get("locate"))
        if nested:
            return nested
    return ""


def _yaml_item_to_action(item: dict[str, Any]) -> dict[str, Any] | None:
    locate_text = _locate_text(item.get("locate")) or _locate_text(item.get("aiTap")) or _locate_text(
        item.get("ai_tap")
    )
    if "aiInput" in item or "ai_input" in item:
        value = item.get("aiInput", item.get("ai_input"))
        return {"kind": "fill", "locate": locate_text, "value": "" if value is None else str(value)}
    if item.get("aiTap") or item.get("ai_tap"):
        text = _locate_text(item.get("aiTap")) or _locate_text(item.get("ai_tap")) or locate_text
        return {
            "kind": "click",
            "locate": locate_text or text,
            "name": text if text and text != locate_text else "",
        }
    if item.get("aiKeyboardPress"):
        return {
            "kind": "press",
            "locate": locate_text,
            "value": str(item.get("aiKeyboardPress") or "Enter"),
        }
    return None


def _planning_to_action(act: dict[str, Any]) -> dict[str, Any] | None:
    kind_name = str(act.get("type") or "").strip().lower()
    param = act.get("param") if isinstance(act.get("param"), dict) else {}
    locate_text = (
        _locate_text(act.get("locate"))
        or _locate_text(param.get("locate"))
        or _locate_text(param.get("prompt"))
        or _locate_text(param)
    )
    if kind_name in ("input", "type", "fill"):
        return {
            "kind": "fill",
            "locate": locate_text,
            "value": str(param.get("value") or param.get("text") or ""),
        }
    if kind_name in ("tap", "click"):
        return {"kind": "click", "locate": locate_text}
    return None


def midscene_dump_to_actions(dump: Any) -> list[dict[str, Any]]:
    """Read Midscene execution dump or an already-normalized action list."""
    if isinstance(dump, list):
        return [x for x in dump if isinstance(x, dict)]
    data = _as_dict(dump)
    if isinstance(data.get("actions"), list):
        return [x for x in data["actions"] if isinstance(x, dict)]
    actions: list[dict[str, Any]] = []
    executions = data.get("executions") if isinstance(data.get("executions"), list) else []
    yaml_actions: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    for execution in executions:
        tasks = _as_dict(execution).get("tasks") or []
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            output = _as_dict(task).get("output")
            output_d = _as_dict(output)
            flows = output_d.get("yamlFlow") or []
            if isinstance(flows, list):
                for item in flows:
                    converted = _yaml_item_to_action(_as_dict(item))
                    if converted:
                        yaml_actions.append(converted)
            raw_actions = output_d.get("actions") or []
            if isinstance(raw_actions, list):
                for act in raw_actions:
                    converted = _planning_to_action(_as_dict(act))
                    if converted:
                        planned.append(converted)
    actions.extend(yaml_actions or planned)
    return actions
