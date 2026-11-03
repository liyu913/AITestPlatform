"""Restricted Python hooks for API case pre/post scripts."""

from __future__ import annotations

from typing import Any

_SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "round": round,
    "str": str,
    "sum": sum,
    "isinstance": isinstance,
}


class ScriptError(ValueError):
    pass


def run_api_script(
    code: str | None,
    *,
    ctx: dict[str, str],
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> None:
    """Execute user script. Mutates ctx / request in place."""
    src = (code or "").strip()
    if not src:
        return
    if len(src) > 20000:
        raise ScriptError("脚本过长")

    def set_var(key: str, value: Any) -> None:
        ctx[str(key)] = "" if value is None else str(value)

    local: dict[str, Any] = {
        "ctx": ctx,
        "request": request if request is not None else {},
        "response": response if response is not None else {},
        "set_var": set_var,
    }
    try:
        exec(src, {"__builtins__": _SAFE_BUILTINS}, local)  # noqa: S102
    except Exception as ex:  # noqa: BLE001
        raise ScriptError(f"脚本执行失败: {ex}") from ex
