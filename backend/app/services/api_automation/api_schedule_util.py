from __future__ import annotations

from datetime import datetime, timezone

from croniter import croniter


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def next_run_iso(cron_expr: str, base: datetime | None = None) -> str:
    base = base or utc_now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    itr = croniter(cron_expr, base)
    nxt = itr.get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=timezone.utc)
    return to_iso(nxt)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
