import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, JSONColumn, TimestampMixin, UUIDPkMixin
from app.db.models.enums import ValidationMode, ValidationState

if TYPE_CHECKING:
    from app.db.models.dataset import Dataset

RULES_VERSION = "1.0"


class SchemaValidation(UUIDPkMixin, TimestampMixin, Base):
    """Schema validation result (PRD section 10).

    Append-only: users re-upload corrected files and need to see what changed.
    The state is a column because it is displayed and filtered everywhere; the
    variable-length issue list stays in JSON.
    """

    __tablename__ = "schema_validations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pass', 'warning', 'blocked')",
            name="state_valid",
        ),
        CheckConstraint("mode IN ('structural', 'analysis')", name="mode_valid"),
        Index("ix_schema_validations_dataset_created", "dataset_id", "created_at"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kpi_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kpi_definitions.id", ondelete="CASCADE")
    )

    mode: Mapped[str] = mapped_column(
        String(20), default=ValidationMode.STRUCTURAL.value, nullable=False
    )
    state: Mapped[str] = mapped_column(String(10), default=ValidationState.PASS.value, nullable=False)

    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    info_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, default=list, nullable=False)
    rules_version: Mapped[str] = mapped_column(String(20), default=RULES_VERSION, nullable=False)

    dataset: Mapped["Dataset"] = relationship(back_populates="validations")
