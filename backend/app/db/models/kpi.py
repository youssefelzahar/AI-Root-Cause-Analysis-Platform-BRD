import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, JSONColumn, TimestampMixin, UUIDPkMixin
from app.db.models.enums import Aggregation, ComparisonPeriod

if TYPE_CHECKING:
    from app.db.models.dataset import Dataset


class KpiDefinition(UUIDPkMixin, TimestampMixin, Base):
    """Normalized KPI definition (PRD section 11).

    Typed columns drive validation and queries; ``definition`` holds the frozen
    source-agnostic contract handed to the RCA engine, in exactly the shape the
    PRD specifies. The duplication is intentional and single-writer - the
    service builds the JSON from the columns.
    """

    __tablename__ = "kpi_definitions"
    __table_args__ = (
        CheckConstraint(
            "aggregation IN (" + ", ".join(f"'{a.value}'" for a in Aggregation) + ")",
            name="aggregation_valid",
        ),
        CheckConstraint(
            "comparison IN (" + ", ".join(f"'{c.value}'" for c in ComparisonPeriod) + ")",
            name="comparison_valid",
        ),
        Index("ix_kpi_definitions_dataset_active", "dataset_id", "is_active"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregation: Mapped[str] = mapped_column(String(20), nullable=False)
    time_column: Mapped[str | None] = mapped_column(String(255))
    dimensions: Mapped[list[str]] = mapped_column(JSONColumn, default=list, nullable=False)
    comparison: Mapped[str] = mapped_column(
        String(30), default=ComparisonPeriod.PREVIOUS_PERIOD.value, nullable=False
    )
    comparison_config: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)
    filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn)

    # The exact contract consumed by the RCA engine.
    definition: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    validation_state: Mapped[str | None] = mapped_column(String(10))

    dataset: Mapped["Dataset"] = relationship(back_populates="kpi_definitions")
