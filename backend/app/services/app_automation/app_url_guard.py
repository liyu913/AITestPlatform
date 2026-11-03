"""Guard Appium server URL to reduce SSRF risk."""

from __future__ import annotations

from urllib.parse import urlparse

_PRIVATE_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
    }
)


class UnsafeAppiumUrlError(ValueError):
    pass


def _is_private_ipv4(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    if any(n < 0 or n > 255 for n in nums):
        return False
    a, b = nums[0], nums[1]
    if a == 10:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 127:
        return True
    return False


def assert_safe_appium_url(url: str) -> str:
    s = (url or "").strip().rstrip("/")
    if not s:
        raise UnsafeAppiumUrlError("appium_server_url 不能为空")
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeAppiumUrlError("appium_server_url 必须以 http:// 或 https:// 开头")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeAppiumUrlError("appium_server_url 缺少主机名")
    if host in _PRIVATE_HOSTS or _is_private_ipv4(host) or host.endswith(".local"):
        return s
    raise UnsafeAppiumUrlError("appium_server_url 仅允许本机或内网地址")
