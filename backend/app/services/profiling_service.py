"""Profiling orchestration: read the file, persist the profile, run validation."""

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.analysis.kpi_heuristics import ColumnFacts, detect
from app.analysis.profiler import ProfileResult, profile_file
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ColumnProfile, Dataset, DatasetProfile, SchemaValidation
from app.db.models.enums import DatasetStatus, FileFormat, ValidationMode, ValidationState
from app.services.materialize import excel_to_parquet, temp_path
from app.services.storage_service import get_storage
from app.services.validation_service import validate_structural

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def run_pipeline(db: Session, dataset_id: uuid.UUID) -> None:
    """Validate then profile a stored dataset.

    Runs in a background task, so it swallows nothing: any failure is recorded
    on the dataset row where the UI can surface it.
    """
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        logger.warning("profiling_dataset_missing", extra={"dataset_id": str(dataset_id)})
        return

    started = time.perf_counter()
    dataset.status = DatasetStatus.PROFILING.value
    dataset.profiling_started_at = _now()
    dataset.error_code = None
    dataset.error_message = None
    db.commit()

    storage = get_storage()
    temp_parquet: Path | None = None

    try:
        if not dataset.storage_key:
            raise ValueError("Dataset has no stored file.")

        with storage.as_local_file(dataset.storage_key) as source_path:
            read_path, read_format = source_path, dataset.file_format

            # xlsx cannot be read by DuckDB, so convert it to the canonical
            # Parquet rendering first and profile that.
            if dataset.file_format == FileFormat.XLSX.value:
                temp_parquet = temp_path(".parquet")
                excel_to_parquet(source_path, temp_parquet)
                read_path, read_format = temp_parquet, FileFormat.PARQUET.value

            result = profile_file(read_path, read_format)

            # Persist the typed Parquet so downstream analysis has one reader.
            if settings.materialize_parquet and not dataset.normalized_key:
                normalized = _materialize(dataset, read_path, read_format, temp_parquet)
                if normalized:
                    dataset.normalized_key = normalized

        duration_ms = int((time.perf_counter() - started) * 1000)
        report = validate_structural(result)
        _persist_profile(db, dataset, result, duration_ms, report.state)
        _persist_validation(db, dataset, report)

        dataset.row_count = result.row_count
        dataset.column_count = result.column_count
        dataset.quality_state = report.state
        dataset.profiling_completed_at = _now()
        dataset.status = (
            DatasetStatus.BLOCKED.value
            if report.state == ValidationState.BLOCKED.value
            else DatasetStatus.PROFILED.value
        )
        db.commit()
        logger.info(
            "profiling_complete",
            extra={
                "dataset_id": str(dataset.id),
                "rows": result.row_count,
                "columns": result.column_count,
                "state": report.state,
                "duration_ms": duration_ms,
            },
        )
    except Exception as exc:
        db.rollback()
        dataset = db.get(Dataset, dataset_id)
        if dataset is not None:
            dataset.status = DatasetStatus.PROFILING_FAILED.value
            dataset.error_code = type(exc).__name__
            dataset.error_message = str(exc)[:2000]
            dataset.profiling_completed_at = _now()
            db.commit()
        logger.exception("profiling_failed", extra={"dataset_id": str(dataset_id)})
    finally:
        if temp_parquet is not None:
            temp_parquet.unlink(missing_ok=True)


def _materialize(
    dataset: Dataset, read_path: Path, read_format: str, temp_parquet: Path | None
) -> str | None:
    """Store the typed Parquet rendering alongside the original file."""
    from app.analysis.duckdb_session import duckdb_connection, quote_literal, source_relation

    storage = get_storage()
    key = f"{dataset.company_id}/normalized/{dataset.id}.parquet"
    output = temp_path(".parquet")
    try:
        if read_format == FileFormat.PARQUET.value and temp_parquet is not None:
            # Already Parquet from the Excel conversion - just store it.
            storage.save_file(key, temp_parquet)
            return key
        with duckdb_connection() as conn:
            relation = source_relation(read_path, read_format)
            conn.execute(
                f"COPY (SELECT * FROM {relation}) TO {quote_literal(output.as_posix())} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        storage.save_file(key, output, move=True)
        return key
    except Exception:
        logger.warning("materialize_failed", extra={"dataset_id": str(dataset.id)})
        return None
    finally:
        output.unlink(missing_ok=True)


def _persist_profile(
    db: Session, dataset: Dataset, result: ProfileResult, duration_ms: int, quality_status: str
) -> DatasetProfile:
    existing = db.scalar(select(DatasetProfile).where(DatasetProfile.dataset_id == dataset.id))
    version = (existing.profile_version + 1) if existing else 1
    if existing:
        db.execute(delete(ColumnProfile).where(ColumnProfile.dataset_profile_id == existing.id))
        db.delete(existing)
        db.flush()

    profile = DatasetProfile(
        dataset_id=dataset.id,
        profile_version=version,
        row_count=result.row_count,
        column_count=result.column_count,
        file_size_bytes=dataset.size_bytes,
        duplicate_row_count=result.duplicate_row_count,
        duplicate_row_pct=result.duplicate_row_pct,
        duplicate_check_skipped=result.duplicate_check_skipped,
        missing_cell_count=result.missing_cell_count,
        missing_cell_pct=result.missing_cell_pct,
        quality_status=quality_status,
        exact_quantiles=result.exact_quantiles,
        duration_ms=duration_ms,
        generated_at=_now(),
    )
    db.add(profile)
    db.flush()

    facts = [
        ColumnFacts(
            name=column.name,
            inferred_type=column.inference.inferred_type,
            raw_type=column.inference.raw_type,
            conversion_confidence=column.inference.confidence,
            null_pct=column.null_pct,
            unique_count=column.unique_count,
            row_count=result.row_count,
            min_value=_maybe_float(column.min_value),
            max_value=_maybe_float(column.max_value),
            distinct_periods=(column.datetime_stats or {}).get("distinct_periods"),
            avg_text_length=column.avg_text_length,
        )
        for column in result.columns
    ]
    candidates = detect(facts)
    measure_scores = {c.column: c for c in candidates.measures}
    dimension_scores = {c.column: c for c in candidates.dimensions}
    time_scores = {c.column: c for c in candidates.time_columns}

    for column in result.columns:
        measure = measure_scores.get(column.name)
        dimension = dimension_scores.get(column.name)
        time_candidate = time_scores.get(column.name)
        reasons = {}
        if measure:
            reasons["measure"] = measure.reasons
        if dimension:
            reasons["dimension"] = dimension.reasons
        if time_candidate:
            reasons["time"] = time_candidate.reasons

        db.add(
            ColumnProfile(
                dataset_profile_id=profile.id,
                dataset_id=dataset.id,
                column_name=column.name,
                ordinal_position=column.ordinal,
                raw_type=column.inference.raw_type,
                inferred_type=column.inference.inferred_type.value,
                semantic_type=candidates.semantic_types.get(column.name).value
                if candidates.semantic_types.get(column.name)
                else "unknown",
                conversion_confidence=column.inference.confidence,
                requires_conversion=column.inference.requires_conversion,
                invalid_value_count=column.inference.invalid_value_count,
                sample_invalid_values=column.inference.sample_invalid_values or None,
                null_count=column.null_count,
                null_pct=column.null_pct,
                unique_count=column.unique_count,
                unique_pct=column.unique_pct,
                min_value=column.min_value,
                max_value=column.max_value,
                mean=column.mean,
                median=column.median,
                stddev=column.stddev,
                outlier_count=column.outlier_count,
                outlier_lower=column.outlier_lower,
                outlier_upper=column.outlier_upper,
                percentiles=column.percentiles or None,
                top_values=column.top_values or None,
                datetime_stats=column.datetime_stats or None,
                kpi_measure_score=measure.score if measure else None,
                kpi_dimension_score=dimension.score if dimension else None,
                kpi_time_score=time_candidate.score if time_candidate else None,
                suggested_aggregation=measure.suggested_aggregation if measure else None,
                candidate_reasons=reasons or None,
            )
        )
    db.flush()
    return profile


def _persist_validation(db: Session, dataset: Dataset, report) -> SchemaValidation:  # noqa: ANN001
    record = SchemaValidation(
        dataset_id=dataset.id,
        mode=ValidationMode.STRUCTURAL.value,
        state=report.state,
        error_count=report.error_count,
        warning_count=report.warning_count,
        info_count=report.info_count,
        issues=[issue.to_dict() for issue in report.issues],
    )
    db.add(record)
    db.flush()
    return record


def _maybe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reconcile_stale_jobs(db: Session) -> int:
    """Mark datasets abandoned by a restart as failed.

    Background tasks run in-process, so a restart mid-profile would otherwise
    leave a dataset stuck in `profiling` forever.
    """
    from datetime import timedelta

    cutoff = _now() - timedelta(minutes=settings.profiling_stale_minutes)
    stale = db.scalars(
        select(Dataset).where(
            Dataset.status.in_([DatasetStatus.PROFILING.value, DatasetStatus.VALIDATING.value]),
            Dataset.profiling_started_at.is_not(None),
            Dataset.profiling_started_at < cutoff,
        )
    ).all()
    for dataset in stale:
        dataset.status = DatasetStatus.PROFILING_FAILED.value
        dataset.error_code = "STALE_JOB"
        dataset.error_message = "Profiling was interrupted. Regenerate the profile to retry."
    if stale:
        db.commit()
    return len(stale)
