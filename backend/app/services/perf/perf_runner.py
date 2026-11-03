"""Programmatic Locust runner for performance scenarios."""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING, Callable
from urllib.parse import urlencode, urlparse

from app.services.api_automation.api_runner import MissingVariableError, render_mapping, render_template
from app.services.api_automation.api_url_guard import UnsafeApiUrlError, join_base_and_path

if TYPE_CHECKING:
    from locust import HttpUser

log = logging.getLogger(__name__)


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _entry_percentiles(entry: Any) -> dict[str, float | None]:
    """Best-effort P50/P90/P95/P99 from Locust StatsEntry."""
    out: dict[str, float | None] = {"p50_ms": None, "p90_ms": None, "p95_ms": None, "p99_ms": None}
    get_response_time_percentile = getattr(entry, "get_response_time_percentile", None)
    if callable(get_response_time_percentile):
        for key, pct in (("p50_ms", 0.5), ("p90_ms", 0.9), ("p95_ms", 0.95), ("p99_ms", 0.99)):
            try:
                val = get_response_time_percentile(pct)
                out[key] = float(val) if val is not None else None
            except Exception:
                out[key] = None
        return out

    rt = getattr(entry, "response_times", None)
    if isinstance(rt, dict) and rt:
        samples: list[float] = []
        for ms, count in rt.items():
            try:
                samples.extend([float(ms)] * int(count))
            except (TypeError, ValueError):
                continue
        samples.sort()
        out["p50_ms"] = _percentile(samples, 50)
        out["p90_ms"] = _percentile(samples, 90)
        out["p95_ms"] = _percentile(samples, 95)
        out["p99_ms"] = _percentile(samples, 99)
    return out


def _stats_entry_to_dict(name: str, entry: Any) -> dict[str, Any]:
    num_requests = int(getattr(entry, "num_requests", 0) or 0)
    num_failures = int(getattr(entry, "num_failures", 0) or 0)
    total_response_time = float(getattr(entry, "total_response_time", 0) or 0)
    avg_ms = (total_response_time / num_requests) if num_requests else None
    fail_ratio = (num_failures / num_requests) if num_requests else 0.0
    total_content_length = float(getattr(entry, "total_content_length", 0) or 0)
    start_time = float(getattr(entry, "start_time", 0) or 0)
    last_request_timestamp = float(getattr(entry, "last_request_timestamp", 0) or 0)
    duration = max(0.001, last_request_timestamp - start_time) if last_request_timestamp and start_time else 0.0
    rps = (num_requests / duration) if duration else 0.0
    percentiles = _entry_percentiles(entry)
    return {
        "name": name,
        "requests": num_requests,
        "failures": num_failures,
        "fail_ratio": round(fail_ratio, 6),
        "avg_ms": round(avg_ms, 3) if avg_ms is not None else None,
        "rps": round(rps, 3),
        "total_content_length": total_content_length,
        **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in percentiles.items()},
    }


def build_request_specs(
    *,
    base_url: str,
    cases: list[dict[str, Any]],
    ctx: dict[str, str],
    env_headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Build Locust-ready request specs from case snapshots."""
    specs: list[dict[str, Any]] = []
    for case in cases:
        weight = max(1, int(case.get("weight") or 1))
        method = str(case.get("method") or "GET").upper()
        path = str(case.get("path") or "/")
        name = str(case.get("name") or f"{method} {path}")
        query = case.get("query") if isinstance(case.get("query"), dict) else {}
        headers = dict(env_headers)
        if isinstance(case.get("headers"), dict):
            headers.update({str(k): str(v) for k, v in case["headers"].items()})
        body_type = str(case.get("body_type") or "none")
        body_text = str(case.get("body_text") or "")
        timeout_ms = int(case.get("timeout_ms") or 15000)

        try:
            rendered_path = render_template(path, ctx)
            rendered_query = render_mapping(query, ctx)
            rendered_headers = render_mapping(headers, ctx)
            rendered_body = render_template(body_text, ctx) if body_type != "none" and body_text else ""
            full_url = join_base_and_path(base_url, rendered_path)
        except (MissingVariableError, UnsafeApiUrlError, ValueError) as ex:
            raise ValueError(f"用例「{name}」准备失败: {ex}") from ex

        parsed = urlparse(full_url)
        path_with_query = parsed.path or "/"
        if rendered_query:
            path_with_query = f"{path_with_query}?{urlencode(rendered_query)}"
        elif parsed.query:
            path_with_query = f"{path_with_query}?{parsed.query}"

        data = None
        json_body = None
        if body_type == "json" and rendered_body.strip():
            import json

            try:
                json_body = json.loads(rendered_body)
            except json.JSONDecodeError:
                data = rendered_body
                if "Content-Type" not in {k.title(): v for k, v in rendered_headers.items()}:
                    rendered_headers["Content-Type"] = "application/json"
        elif body_type == "text" and rendered_body:
            data = rendered_body

        specs.append(
            {
                "name": name,
                "method": method,
                "path": path_with_query,
                "headers": rendered_headers,
                "data": data,
                "json": json_body,
                "timeout": timeout_ms / 1000.0,
                "weight": weight,
            }
        )
    return specs


def create_user_class(host: str, specs: list[dict[str, Any]], think_time_ms: int) -> type:
    from locust import HttpUser, between, constant, task

    wait = between(think_time_ms / 1000.0, think_time_ms / 1000.0) if think_time_ms > 0 else constant(0)

    class ScenarioUser(HttpUser):
        wait_time = wait
        abstract = False

        def on_start(self) -> None:
            self.client.verify = True

    ScenarioUser.host = host

    for spec in specs:
        weight = int(spec["weight"])

        def _make_task(s: dict[str, Any]):
            @task(weight)
            def _task(self: HttpUser, _s: dict[str, Any] = s) -> None:
                kwargs: dict[str, Any] = {
                    "name": _s["name"],
                    "headers": _s["headers"],
                    "timeout": _s["timeout"],
                    "allow_redirects": False,
                }
                if _s.get("json") is not None:
                    kwargs["json"] = _s["json"]
                elif _s.get("data") is not None:
                    kwargs["data"] = _s["data"]
                with self.client.request(_s["method"], _s["path"], catch_response=True, **kwargs) as resp:
                    if resp.status_code >= 400:
                        resp.failure(f"HTTP {resp.status_code}")
                    else:
                        resp.success()

            return _task

        setattr(ScenarioUser, f"task_{len(ScenarioUser.__dict__)}_{spec['name'][:40]}", _make_task(spec))

    return ScenarioUser


def run_locust_load(
    *,
    host: str,
    specs: list[dict[str, Any]],
    users: int,
    spawn_rate: float,
    run_time_sec: int,
    think_time_ms: int = 0,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if not specs:
        raise ValueError("没有可执行的请求")

    from locust.env import Environment
    from locust.stats import stats_history

    user_cls = create_user_class(host, specs, think_time_ms)
    env = Environment(user_classes=[user_cls])
    env.create_local_runner()
    assert env.runner is not None

    try:
        gevent = __import__("gevent")
        hist = gevent.spawn(stats_history, env.runner)
    except Exception:
        hist = None
        gevent = None

    cancelled = False
    started = time.perf_counter()
    try:
        env.runner.start(users, spawn_rate=spawn_rate)
        remaining = max(1, int(run_time_sec))
        while remaining > 0:
            if should_stop and should_stop():
                cancelled = True
                break
            step = 1 if remaining > 1 else remaining
            if gevent is not None:
                gevent.sleep(step)
            else:
                time.sleep(step)
            remaining -= step
        env.runner.quit()
        if env.runner.greenlet is not None:
            env.runner.greenlet.join(timeout=30)
    finally:
        elapsed = time.perf_counter() - started
        if hist is not None:
            try:
                hist.kill(block=False)
            except Exception:
                pass
        try:
            env.runner.quit()
        except Exception:
            pass

    total = env.stats.total
    total_dict = _stats_entry_to_dict("Aggregated", total)
    if elapsed > 0 and total_dict["requests"]:
        total_dict["rps"] = round(total_dict["requests"] / elapsed, 3)

    endpoints: list[dict[str, Any]] = []
    for key, entry in env.stats.entries.items():
        name = key[0] if isinstance(key, tuple) else str(key)
        method = key[1] if isinstance(key, tuple) and len(key) > 1 else ""
        label = f"{method} {name}".strip() if method and method != "Aggregated" else name
        if label == "Aggregated" or not getattr(entry, "num_requests", 0):
            continue
        endpoints.append(_stats_entry_to_dict(label, entry))
    endpoints.sort(key=lambda x: x["requests"], reverse=True)

    return {
        "total_requests": total_dict["requests"],
        "failures": total_dict["failures"],
        "fail_ratio": total_dict["fail_ratio"],
        "rps": total_dict["rps"],
        "avg_ms": total_dict["avg_ms"],
        "p50_ms": total_dict["p50_ms"],
        "p90_ms": total_dict["p90_ms"],
        "p95_ms": total_dict["p95_ms"],
        "p99_ms": total_dict["p99_ms"],
        "elapsed_sec": round(elapsed, 3),
        "endpoints": endpoints,
        "cancelled": cancelled,
    }


def evaluate_sla(metrics: dict[str, Any], sla: dict[str, Any] | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    passed = True
    sla = sla or {}

    max_p95 = sla.get("max_p95_ms")
    if max_p95 is not None:
        actual = metrics.get("p95_ms")
        ok = actual is not None and float(actual) <= float(max_p95)
        checks.append(
            {
                "name": "max_p95_ms",
                "expected": float(max_p95),
                "actual": actual,
                "ok": ok,
            }
        )
        if not ok:
            passed = False

    max_err = sla.get("max_error_rate")
    if max_err is not None:
        actual = float(metrics.get("fail_ratio") or 0)
        ok = actual <= float(max_err)
        checks.append(
            {
                "name": "max_error_rate",
                "expected": float(max_err),
                "actual": actual,
                "ok": ok,
            }
        )
        if not ok:
            passed = False

    if not checks:
        return {"passed": True, "checks": []}
    return {"passed": passed, "checks": checks}
