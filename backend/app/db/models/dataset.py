import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import Base, JSONColumn, TimestampMixin, UUIDPkMixin
from app.db.models.enums import DatasetStatus, FileFormat, SourceType, UploadStatus, ValidationState

if TYPE_CHECKING:
    from app.db.models.kpi import KpiDefinition
    from app.db.models.profile import DatasetProfile
    from app.db.models.validation import SchemaValidation


def _check(column: str, enum: type, name: str) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class Dataset(UUIDPkMixin, TimestampMixin, Base):
    """Dataset metadata (PRD section 7).

    The primary key doubles as the physical storage UUID, which is what keeps
    the original filename out of the storage path (PRD principle 2).
    """

    __tablename__ = "datasets"
    __table_args__ = (
        _check("source_type", SourceType, "source_type_valid"),
        _check("file_format", FileFormat, "file_format_valid"),
        _check("upload_status", UploadStatus, "upload_status_valid"),
        _check("status", DatasetStatus, "status_valid"),
        Index("ix_datasets_company_created", "company_id", "created_at"),
        Index("ix_datasets_company_status", "company_id", "status"),
        Index("ix_datasets_company_checksum", "company_id", "checksum_sha256"),
    )

    # --- ownership (PRD minimum metadata) --------------------------------
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # --- identity --------------------------------------------------------
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Retained as metadata only; never used to build a filesystem path.
    original_filename: Mapped[str | None] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- storage ---------------------------------------------------------
    storage_key: Mapped[str | None] = mapped_column(String(512))
    # Canonical typed Parquet rendering; every source type converges here so
    # the RCA engine has exactly one reader.
    normalized_key: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))

    # --- shape -----------------------------------------------------------
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    column_count: Mapped[int | None] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # --- lifecycle -------------------------------------------------------
    upload_status: Mapped[str] = mapped_column(
        String(20), default=UploadStatus.PENDING.value, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default=DatasetStatus.PENDING_UPLOAD.value, nullable=False
    )
    # Denormalised copy of the latest structural validation state so the list
    # endpoint can filter on quality without joining.
    quality_state: Mapped[str | None] = mapped_column(String(10))

    ingest_options: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn)

    # --- SQL Server provenance -------------------------------------------
    source_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sql_connections.id", ondelete="SET NULL")
    )
    # The SELECT text only. Credentials live on the connection row.
    source_query: Mapped[str | None] = mapped_column(Text)

    # --- errors / jobs ---------------------------------------------------
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    profiling_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profiling_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped["DatasetProfile | None"] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", uselist=False
    )
    validations: Mapped[list["SchemaValidation"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )
    kpi_definitions: Mapped[list["KpiDefinition"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan"
    )

    @property
    def is_analysis_ready(self) -> bool:
        return self.status == DatasetStatus.ANALYSIS_READY.value

    @property
    def is_blocked(self) -> bool:
        return self.quality_state == ValidationState.BLOCKED.value
