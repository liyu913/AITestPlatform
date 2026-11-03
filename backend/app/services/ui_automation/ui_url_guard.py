"""Join environment base_url with relative UI path; block SSRF-ish absolute URLs."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


class UnsafeUiUrlError(ValueError):
    pass


def join_base_and_path(base_url: str, path: str) -> str:
    base = (base_url or "").strip()
    p = (path or "").strip()
    if not base:
        raise UnsafeUiUrlError("base_url 为空")
    if not (base.startswith("http://") or base.startswith("https://")):
        raise UnsafeUiUrlError("base_url 必须以 http:// 或 https:// 开头")
    if not p.startswith("/"):
        raise UnsafeUiUrlError("path 必须以 / 开头")
    if "://" in p or p.startswith("//"):
        raise UnsafeUiUrlError("path 不能是绝对 URL")
    if ".." in p.split("/"):
        raise UnsafeUiUrlError("path 不允许包含 ..")

    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UnsafeUiUrlError("base_url 无效")

    # path may include query/fragment: /login?x=1
    if "?" in p or "#" in p:
        rel = urlparse("http://dummy" + p)
        full_path = (parsed.path.rstrip("/") + rel.path) if parsed.path.rstrip("/") else rel.path
        return urlunparse((parsed.scheme, parsed.netloc, full_path, "", rel.query, rel.fragment))

    base_path = parsed.path.rstrip("/")
    full_path = f"{base_path}{p}" if base_path else p
    return urlunparse((parsed.scheme, parsed.netloc, full_path, "", "", ""))


def assert_same_host(base_url: str, current_url: str) -> None:
    base = urlparse(base_url)
    cur = urlparse(current_url)
    if cur.scheme not in ("http", "https"):
        raise UnsafeUiUrlError("页面跳转到了非 http(s) 地址")
    if (cur.hostname or "").lower() != (base.hostname or "").lower():
        raise UnsafeUiUrlError("页面跳转到了环境 base_url 之外的主机")
