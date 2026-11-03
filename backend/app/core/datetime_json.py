"""Beijing (Asia/Shanghai) datetime helpers: store/display as东八墙钟."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

BEIJING = ZoneInfo("Asia/Shanghai")


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def ensure_beijing(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING)


def to_beijing_iso(dt: datetime) -> str:
    """ISO with +08:00 (or naive wall clock stripped of micros for strings)."""
    return ensure_beijing(dt).replace(microsecond=0).isoformat()


class BeijingDateTime(TypeDecorator):
    """MySQL DATETIME as naive Beijing wall clock; aware +08:00 in Python."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(BEIJING).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=BEIJING)
        return value.astimezone(BEIJING)


# Backward alias while migrating imports
UtcDateTime = BeijingDateTime
