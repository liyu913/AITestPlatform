import httpx
import pytest

from app.errors import AppError
from app.services.ai.ai_http_errors import check_ai_http_response


def test_check_ai_http_response_401():
    r = httpx.Response(401, request=httpx.Request("POST", "https://example.com"))
    with pytest.raises(AppError) as exc:
        check_ai_http_response(r, "LLM")
    assert exc.value.status_code == 400
    assert "API Key 无效" in exc.value.message


def test_check_ai_http_response_ok():
    r = httpx.Response(200, request=httpx.Request("POST", "https://example.com"))
    check_ai_http_response(r, "LLM")
