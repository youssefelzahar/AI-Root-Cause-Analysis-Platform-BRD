"""Dataset registration, ingestion and lifecycle."""

import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, UnsupportedMediaTypeError
from app.core.logging import get_logger
from app.db.models import Dataset
from app.db.models.enums import DatasetStatus, FileFormat, SourceType, UploadStatus
from app.services.storage_service import get_storage
from app.storage.base import FileTooLarge

logger = get_logger(__name__)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

EXTENSION_FORMAT = {
    ".csv": FileFormat.CSV.value,
    ".tsv": FileFormat.TSV.value,
    ".txt": FileFormat.CSV.value,
    ".xlsx": FileFormat.XLSX.value,
}

FORMAT_SOURCE = {
    FileFormat.CSV.value: SourceType.CSV.value,
    FileFormat.TSV.value: SourceType.CSV.value,
    FileFormat.XLSX.value: SourceType.EXCEL.value,
    FileFormat.PARQUET.value: SourceType.SQLSERVER.value,
}


def sanitize_filename(raw: str | None) -> str:
    """Clean a filename for use as *metadata only*.

    The result never reaches the filesystem - the storage key is built from
    UUIDs (PRD principle 2) - but it is still stripped so a hostile name cannot
    poison logs or a Content-Disposition header.
    """
    if not raw:
        return "upload"
    name = PurePosixPath(raw.replace("\\", "/")).name
    name = _CONTROL_CHARS.sub("", name).strip()
    return name[:255] or "upload"


def resolve_format(filename: str) -> tuple[str, str]:
    """Map a filename to (file_format, extension), rejecting anything else."""
    suffix = PurePosixPath(filename.lower()).suffix
    if suffix not in settings.allowed_upload_extensions or suffix not in EXTENSION_FORMAT:
        raise UnsupportedMediaTypeError(
            f"'{suffix or filename}' is not a supported file type. "
            f"Allowed: {', '.join(sorted(settings.allowed_upload_extensions))}.",
            details={"allowed_extensions": sorted(settings.allowed_upload_extensions)},
        )
    return EXTENSION_FORMAT[suffix], suffix


def create_dataset_row(
    db: Session,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str,
    original_filename: str | None,
    file_format: str,
    description: str | None = None,
    source_type: str | None = None,
) -> Dataset:
    """Insert the metadata row *before* any bytes are written.

    Committing this first means a stored file can never exist without a
    corresponding metadata record.
    """
    dataset = Dataset(
        company_id=company_id,
        user_id=user_id,
        name=name,
        description=description,
        source_type=source_type or FORMAT_SOURCE.get(file_format, SourceType.CSV.value),
        original_filename=original_filename,
        file_format=file_format,
        upload_status=UploadStatus.PENDING.value,
        status=DatasetStatus.PENDING_UPLOAD.value,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


async def store_upload(
    db: Session,
    dataset: Dataset,
    chunks: AsyncIterator[bytes],
    extension: str,
) -> Dataset:
    """Stream an upload into storage and finalise the metadata."""
    storage = get_storage()
    key = storage.build_key(
        company_id=dataset.company_id,
        dataset_id=dataset.id,
        extension=extension,
        created_at=dataset.created_at,
    )

    # xlsx must be fully materialised by openpyxl to be read, so it gets a
    # tighter cap than the PRD's CSV-oriented 200 MB.
    max_bytes = (
        settings.excel_max_bytes
        if dataset.file_format == FileFormat.XLSX.value
        else settings.max_upload_bytes
    )

    dataset.upload_status = UploadStatus.UPLOADING.value
    db.commit()

    try:
        stored = await storage.save_stream(
            key, chunks, max_bytes=max_bytes, chunk_size=settings.upload_chunk_bytes
        )
    except FileTooLarge:
        _fail_upload(db, dataset, "FILE_TOO_LARGE", "The file exceeds the maximum upload size.")
        raise
    except Exception as exc:
        _fail_upload(db, dataset, type(exc).__name__, str(exc))
        raise

    if stored.size_bytes == 0:
        storage.delete(key)
        _fail_upload(db, dataset, "EMPTY_FILE", "The uploaded file is empty.")
        raise AppError("The uploaded file is empty.", code="EMPTY_FILE")

    dataset.storage_key = stored.key
    dataset.size_bytes = stored.size_bytes
    dataset.checksum_sha256 = stored.sha256
    dataset.upload_status = UploadStatus.STORED.value
    dataset.status = DatasetStatus.UPLOADED.value
    db.commit()
    db.refresh(dataset)
    return dataset


def _fail_upload(db: Session, dataset: Dataset, code: str, message: str) -> None:
    db.rollback()
    dataset = db.get(Dataset, dataset.id)
    if dataset is None:
        return
    dataset.upload_status = UploadStatus.FAILED.value
    dataset.status = DatasetStatus.UPLOAD_FAILED.value
    dataset.error_code = code
    dataset.error_message = message[:2000]
    db.commit()


def find_duplicate(
    db: Session, company_id: uuid.UUID, checksum: str, exclude_id: uuid.UUID
) -> Dataset | None:
    return db.scalar(
        select(Dataset).where(
            Dataset.company_id == company_id,
            Dataset.checksum_sha256 == checksum,
            Dataset.id != exclude_id,
            Dataset.deleted_at.is_(None),
        )
    )


def get_dataset(db: Session, dataset_id: uuid.UUID, company_id: uuid.UUID) -> Dataset:
    """Fetch a dataset scoped to its company.

    Cross-company access returns 404 rather than 403 so the API never confirms
    that another tenant's dataset exists.
    """
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.company_id == company_id,
            Dataset.deleted_at.is_(None),
        )
    )
    if dataset is None:
        raise NotFoundError("Dataset not found.", code="DATASET_NOT_FOUND")
    return dataset


def delete_dataset(db: Session, dataset: Dataset) -> None:
    if dataset.status in {DatasetStatus.PROFILING.value, DatasetStatus.VALIDATING.value}:
        raise ConflictError(
            "This dataset is still being processed. Try again once profiling finishes.",
            code="PROFILING_IN_PROGRESS",
        )

    storage = get_storage()
    for key in (dataset.storage_key, dataset.normalized_key):
        if key:
            try:
                storage.delete(key)
            except Exception:
                logger.warning("storage_delete_failed", extra={"key": key})

    dataset.deleted_at = datetime.now(UTC)
    db.delete(dataset)
    db.commit()
