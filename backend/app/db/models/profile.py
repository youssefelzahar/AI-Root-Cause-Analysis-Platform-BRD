import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, JSONColumn, TimestampMixin, UUIDPkMixin

if TYPE_CHECKING:
    from app.db.models.dataset import Dataset


class DatasetProfile(UUIDPkMixin, TimestampMixin, Base):
    """Dataset-level profile (PRD section 9).

    Held in its own table rather than on ``datasets`` so the list endpoint does
    not drag profile data around, and so a profile can be regenerated
    independently of the dataset row.
    """

    __tablename__ = "dataset_profiles"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Null when skipped on very wide datasets - recorded honestly rather than
    # reported as zero. See duplicate_check_skipped.
    duplicate_row_count: Mapped[int | None] = mapped_column(BigInteger)
    duplicate_row_pct: Mapped[float | None] = mapped_column(Float)
    duplicate_check_skipped: Mapped[bool] = mapped_column(default=False, nullable=False)
    missing_cell_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    missing_cell_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # The PRD's "dataset status" inside the profile: the quality verdict, which
    # is distinct from datasets.status (the pipeline state).
    quality_status: Mapped[str | None] = mapped_column(String(20))

    engine: Mapped[str] = mapped_column(String(20), default="duckdb", nullable=False)
    exact_quantiles: Mapped[bool] = mapped_column(default=True, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)

    dataset: Mapped["Dataset"] = relationship(back_populates="profile")
    columns: Mapped[list["ColumnProfile"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", order_by="ColumnProfile.ordinal_position"
    )


class ColumnProfile(UUIDPkMixin, Base):
    """Per-column profile.

    One row per column rather than a JSON blob, so the KPI candidate query can
    rank in SQL and the Columns tab can filter/paginate server-side. The
    type-specific statistics - whose shape differs entirely between numeric,
    categorical and datetime columns - stay in JSON.
    """

    __tablename__ = "column_profiles"
    __table_args__ = (
        UniqueConstraint("dataset_profile_id", "column_name", name="uq_column_profiles_profile_column"),
        Index("ix_column_profiles_dataset_semantic", "dataset_id", "semantic_type"),
    )

    dataset_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised so KPI-candidate queries skip a join.
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)

    raw_type: Mapped[str] = mapped_column(String(50), nullable=False)
    inferred_type: Mapped[str] = mapped_column(String(20), nullable=False)
    semantic_type: Mapped[str] = mapped_column(String(20), nullable=False)
    conversion_confidence: Mapped[float | None] = mapped_column(Float)
    requires_conversion: Mapped[bool] = mapped_column(default=False, nullable=False)
    invalid_value_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    sample_invalid_values: Mapped[list[str] | None] = mapped_column(JSONColumn)

    null_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    null_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unique_count: Mapped[int | None] = mapped_column(BigInteger)
    unique_pct: Mapped[float | None] = mapped_column(Float)

    # Text representations so one pair of columns works for every type.
    min_value: Mapped[str | None] = mapped_column(String(255))
    max_value: Mapped[str | None] = mapped_column(String(255))

    mean: Mapped[float | None] = mapped_column(Float)
    median: Mapped[float | None] = mapped_column(Float)
    stddev: Mapped[float | None] = mapped_column(Float)

    # PRD "outlier indicators" is undefined; implemented as IQR 1.5x fences.
    outlier_count: Mapped[int | None] = mapped_column(BigInteger)
    outlier_lower: Mapped[float | None] = mapped_column(Float)
    outlier_upper: Mapped[float | None] = mapped_column(Float)

    percentiles: Mapped[dict[str, float] | None] = mapped_column(JSONColumn)
    top_values: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn)
    datetime_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)

    # Cached KPI-detection output (PRD section 11).
    kpi_measure_score: Mapped[float | None] = mapped_column(Float)
    kpi_dimension_score: Mapped[float | None] = mapped_column(Float)
    kpi_time_score: Mapped[float | None] = mapped_column(Float)
    suggested_aggregation: Mapped[str | None] = mapped_column(String(20))
    candidate_reasons: Mapped[dict[str, list[str]] | None] = mapped_column(JSONColumn)

    profile: Mapped[DatasetProfile] = relationship(back_populates="columns")
