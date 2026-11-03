from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class TestCase(Base, TimestampMixin):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)
    precondition: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    case_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    overall_expected: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="draft")
    tags_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    steps = relationship(
        "TestCaseStep",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="TestCaseStep.order",
    )


class TestCaseStep(Base, TimestampMixin):
    __tablename__ = "test_case_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"), index=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)

    case = relationship("TestCase", back_populates="steps")
