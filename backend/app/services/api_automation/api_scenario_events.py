"""In-process event bus for scenario agent SSE (MemorySaver companion)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable

from app.core.datetime_json import now_beijing, to_beijing_iso


def _utc_now() -> str:
    return to_beijing_iso(now_beijing())


class SessionEventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._waiters: dict[str, list[threading.Event]] = defaultdict(list)

    def append(self, thread_id: str, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("ts", _utc_now())
        with self._lock:
            self._events[thread_id].append(payload)
            waiters = list(self._waiters.get(thread_id, []))
        for w in waiters:
            w.set()
        return payload

    def snapshot(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events.get(thread_id, []))

    def replace(self, thread_id: str, events: list[dict[str, Any]]) -> None:
        with self._lock:
            self._events[thread_id] = list(events)

    def wait_after(self, thread_id: str, index: int, timeout: float = 25.0) -> list[dict[str, Any]]:
        """Block until new events appear after index, or timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                events = self._events.get(thread_id, [])
                if len(events) > index:
                    return list(events)
                waiter = threading.Event()
                self._waiters[thread_id].append(waiter)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    self._waiters[thread_id] = [w for w in self._waiters[thread_id] if w is not waiter]
                return self.snapshot(thread_id)
            waiter.wait(timeout=min(remaining, 1.0))
            with self._lock:
                self._waiters[thread_id] = [w for w in self._waiters[thread_id] if w is not waiter]


_bus = SessionEventBus()


def get_event_bus() -> SessionEventBus:
    return _bus


def emit_log(
    thread_id: str,
    *,
    node: str,
    message: str,
    level: str = "info",
    data: dict[str, Any] | None = None,
    persist: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    ev = get_event_bus().append(
        thread_id,
        {
            "type": "log",
            "level": level,
            "node": node,
            "message": message,
            "data": data or {},
        },
    )
    if persist:
        persist(ev)
    return ev
