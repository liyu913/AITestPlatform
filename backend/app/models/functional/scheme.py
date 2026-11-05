from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TestScheme(Base, TimestampMixin):
    """A test strategy/scheme owned by a project.

    Plans reference a *scheme version*, so editing the scheme never silently
    changes what an existing plan was approved against.
    """

    __tablename__ = "test_schemes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    scope: Mapped[str] = mapped_column(Text, default="")
    strategy: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[str] = mapped_column(Text, default="")
    methods: Mapped[str] = mapped_column(Text, default="")
    exit_criteria: Mapped[str] = mapped_column(Text, default="")
    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    versions = relationship(
        "TestSchemeVersion",
        back_populates="scheme",
        cascade="all, delete-orphan",
        order_by="TestSchemeVersion.version_no",
    )


class TestSchemeVersion(Base, TimestampMixin):
    __tablename__ = "test_scheme_versions"
    __table_args__ = (UniqueConstraint("scheme_id", "version_no", name="uq_scheme_version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scheme_id: Mapped[int] = mapped_column(
        ForeignKey("test_schemes.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    change_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    scheme = relationship("TestScheme", back_populates="versions")


__all__ = ["TestScheme", "TestSchemeVersion"]
