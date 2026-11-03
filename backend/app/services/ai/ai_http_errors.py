"""Map httpx responses from LLM/Embedding vendors to user-facing AppError (Chinese)."""

from __future__ import annotations

import logging

import httpx

from app.errors import AppError

log = logging.getLogger(__name__)


def _response_snippet(response: httpx.Response, limit: int = 500) -> str:
    try:
        text = response.text
    except Exception:
        return "<unreadable>"
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def check_ai_http_response(response: httpx.Response, service: str) -> None:
    """Raise AppError when the upstream AI HTTP response is not successful."""
    if response.is_success:
        return
    status = response.status_code
    req_url = str(response.request.url) if response.request is not None else "?"
    log.warning(
        "AI upstream error service=%s status=%s url=%s body=%s",
        service,
        status,
        req_url,
        _response_snippet(response),
    )
    if status == 401:
        raise AppError(
            "bad_request",
            f"{service} API Key 无效或未授权，请检查 DashScope 密钥配置",
            status_code=400,
        )
    if status == 403:
        raise AppError(
            "bad_request",
            f"{service} 访问被拒绝，请确认账号已开通对应模型权限",
            status_code=400,
        )
    if status == 429:
        raise AppError(
            "bad_request",
            f"{service} 请求过于频繁或额度不足，请稍后重试",
            status_code=429,
        )
    raise AppError(
        "bad_gateway",
        f"{service} 接口返回错误（HTTP {status}）",
        status_code=502,
    )
