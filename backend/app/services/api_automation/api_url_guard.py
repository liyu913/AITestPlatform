"""Join environment base_url with relative case path; block SSRF-ish absolute URLs."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


class UnsafeApiUrlError(ValueError):
    pass


def join_base_and_path(base_url: str, path: str) -> str:
    base = (base_url or "").strip()
    p = (path or "").strip()
    if not base:
        raise UnsafeApiUrlError("base_url 为空")
    if not (base.startswith("http://") or base.startswith("https://")):
        raise UnsafeApiUrlError("base_url 必须以 http:// 或 https:// 开头")
    if not p.startswith("/"):
        raise UnsafeApiUrlError("path 必须以 / 开头")
    if "://" in p or p.startswith("//"):
        raise UnsafeApiUrlError("path 不能是绝对 URL")
    if ".." in p.split("/"):
        raise UnsafeApiUrlError("path 不允许包含 ..")

    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise UnsafeApiUrlError("base_url 无效")

    base_path = parsed.path.rstrip("/")
    full_path = f"{base_path}{p}" if base_path else p
    joined = urlunparse((parsed.scheme, parsed.netloc, full_path, "", "", ""))
    return joined
