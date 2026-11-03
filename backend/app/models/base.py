import datetime

from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.datetime_json import BeijingDateTime

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}
metadata_obj = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    metadata = metadata_obj


class TimestampMixin:
    created_at: Mapped[datetime.datetime] = mapped_column(BeijingDateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        BeijingDateTime, server_default=func.now(), onupdate=func.now()
    )
