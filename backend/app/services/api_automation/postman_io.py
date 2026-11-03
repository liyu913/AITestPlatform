"""Postman Collection v2.1 import / export."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

_VAR_RE = re.compile(r"\{\{([^}]+)\}\}")


def _url_to_path(url: Any) -> tuple[str, dict[str, str]]:
    query: dict[str, str] = {}
    if isinstance(url, dict):
        raw = url.get("raw") or ""
        path_parts = url.get("path") or []
        if isinstance(path_parts, list) and path_parts:
            path = "/" + "/".join(str(p).lstrip("/") for p in path_parts)
        else:
            parsed = urlparse(str(raw))
            path = parsed.path or "/"
        for q in url.get("query") or []:
            if isinstance(q, dict) and q.get("key"):
                query[str(q["key"])] = str(q.get("value") or "")
        return path if path.startswith("/") else f"/{path}", query
    parsed = urlparse(str(url or "/"))
    path = parsed.path or "/"
    if parsed.query:
        for part in parsed.query.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                query[k] = v
    return path, query


def _walk_items(items: list[Any], out: list[dict[str, Any]], folder: str = "") -> None:
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "request")
        if "item" in it and isinstance(it["item"], list):
            _walk_items(it["item"], out, folder=f"{folder}/{name}" if folder else name)
            continue
        req = it.get("request")
        if not isinstance(req, dict):
            continue
        method = str(req.get("method") or "GET").upper()
        path, query = _url_to_path(req.get("url"))
        headers: dict[str, str] = {}
        for h in req.get("header") or []:
            if isinstance(h, dict) and h.get("key"):
                headers[str(h["key"])] = str(h.get("value") or "")
        body_type = "none"
        body = ""
        b = req.get("body")
        if isinstance(b, dict):
            mode = b.get("mode")
            if mode == "raw":
                raw = str(b.get("raw") or "")
                lang = ((b.get("options") or {}).get("raw") or {}).get("language")
                body = raw
                body_type = "json" if lang == "json" or raw.strip().startswith("{") else "text"
            elif mode == "urlencoded":
                body_type = "text"
                pairs = []
                for row in b.get("urlencoded") or []:
                    if isinstance(row, dict) and row.get("key"):
                        pairs.append(f"{row['key']}={row.get('value') or ''}")
                body = "&".join(pairs)
        out.append(
            {
                "name": f"{folder}/{name}" if folder else name,
                "method": method,
                "path": path,
                "query": query,
                "headers": headers,
                "body_type": body_type,
                "body": body,
                "assertions": [{"type": "status", "expected": 200}],
                "extractors": [],
                "timeout_ms": 15000,
                "status": "draft",
            }
        )


def import_postman_collection(raw: str) -> list[dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Postman Collection 必须是 JSON 对象")
    info = data.get("info") if isinstance(data.get("info"), dict) else {}
    schema = str(info.get("schema") or "")
    if "collection" not in schema.lower() and "item" not in data:
        raise ValueError("不是有效的 Postman Collection")
    cases: list[dict[str, Any]] = []
    _walk_items(data.get("item") or [], cases)
    return cases


def export_postman_collection(
    *,
    name: str,
    cases: list[dict[str, Any]],
    base_url: str = "{{base_url}}",
) -> dict[str, Any]:
    items = []
    for c in cases:
        path = c.get("path") or "/"
        headers = [{"key": k, "value": v} for k, v in (c.get("headers") or {}).items()]
        query = [{"key": k, "value": v} for k, v in (c.get("query") or {}).items()]
        body: dict[str, Any] | None = None
        bt = c.get("body_type") or "none"
        if bt == "json":
            body = {
                "mode": "raw",
                "raw": c.get("body") or "",
                "options": {"raw": {"language": "json"}},
            }
        elif bt == "text" and c.get("body"):
            body = {"mode": "raw", "raw": c.get("body") or ""}
        raw_url = f"{base_url.rstrip('/')}{path}"
        items.append(
            {
                "name": c.get("name") or path,
                "request": {
                    "method": c.get("method") or "GET",
                    "header": headers,
                    "url": {
                        "raw": raw_url,
                        "host": ["{{base_url}}"],
                        "path": [p for p in path.strip("/").split("/") if p],
                        "query": query,
                    },
                    **({"body": body} if body else {}),
                },
            }
        )
    return {
        "info": {
            "name": name,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
        "variable": [{"key": "base_url", "value": base_url}],
    }
