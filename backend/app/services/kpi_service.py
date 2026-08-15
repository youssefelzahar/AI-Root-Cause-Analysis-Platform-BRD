"""KPI candidate detection and KPI definition management (PRD section 11)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.kpi_heuristics import ColumnFacts, KpiCandidates, detect, recommended_default
from app.core.exceptions import NotFoundError, NotReadyError, ValidationError
from app.db.models import ColumnProfile, Dataset, DatasetProfile, KpiDefinition, SchemaValidation
from app.db.models.enums import DatasetStatus, InferredType, ValidationMode, ValidationState
from app.services.validation_service import validate_for_kpi


class _ProfileView:
    """Adapts persisted profile rows to the shape the validator expects."""

    def __init__(self, profile: DatasetProfile, columns: list[ColumnProfile]) -> None:
        self.row_count = profile.row_count
        self.column_count = profile.column_count
        self.missing_cell_pct = profile.missing_cell_pct
        self.duplicate_row_pct = profile.duplicate_row_pct
        self.columns = [_ColumnView(c) for c in columns]


class _ColumnView:
    def __init__(self, column: ColumnProfile) -> None:
        self.name = column.column_name
        self.ordinal = column.ordinal_position
        self.null_pct = column.null_pct
        self.unique_count = column.unique_count
        self.percentiles = column.percentiles or {}
        self.datetime_stats = column.datetime_stats or {}
        self.inference = _InferenceView(column)


class _InferenceView:
    def __init__(self, column: ColumnProfile) -> None:
        self.inferred_type = InferredType(column.inferred_type)
        self.raw_type = column.raw_type
        self.confidence = column.conversion_confidence or 0.0
        self.requires_conversion = column.requires_conversion
        self.invalid_value_count = column.invalid_value_count
        self.sample_invalid_values = column.sample_invalid_values or []

    @property
    def is_numeric(self) -> bool:
        return self.inferred_type in {InferredType.INTEGER, InferredType.NUMERIC}

    @property
    def is_temporal(self) -> bool:
        return self.inferred_type in {InferredType.DATE, InferredType.DATETIME}


def load_profile_view(db: Session, dataset: Dataset) -> _ProfileView:
    profile = db.scalar(select(DatasetProfile).where(DatasetProfile.dataset_id == dataset.id))
    if profile is None:
        raise NotReadyError(
            "This dataset has not been profiled yet.", code="PROFILE_NOT_READY"
        )
    columns = db.scalars(
        select(ColumnProfile)
        .where(ColumnProfile.dataset_profile_id == profile.id)
        .order_by(ColumnProfile.ordinal_position)
    ).all()
    return _ProfileView(profile, list(columns))


def get_candidates(db: Session, dataset: Dataset) -> tuple[KpiCandidates, dict[str, Any], str | None]:
    profile = db.scalar(select(DatasetProfile).where(DatasetProfile.dataset_id == dataset.id))
    if profile is None:
        raise NotReadyError("This dataset has not been profiled yet.", code="PROFILE_NOT_READY")

    columns = db.scalars(
        select(ColumnProfile)
        .where(ColumnProfile.dataset_profile_id == profile.id)
        .order_by(ColumnProfile.ordinal_position)
    ).all()

    facts = [
        ColumnFacts(
            name=c.column_name,
            inferred_type=InferredType(c.inferred_type),
            raw_type=c.raw_type,
            conversion_confidence=c.conversion_confidence or 0.0,
            null_pct=c.null_pct,
            unique_count=c.unique_count,
            row_count=profile.row_count,
            min_value=_maybe_float(c.min_value),
            max_value=_maybe_float(c.max_value),
            distinct_periods=(c.datetime_stats or {}).get("distinct_periods"),
        )
        for c in columns
    ]
    candidates = detect(facts)

    frequency = None
    for c in columns:
        detected = (c.datetime_stats or {}).get("detected_frequency")
        if detected:
            frequency = detected
            break

    return candidates, recommended_default(candidates, frequency), frequency


def normalize_definition(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce exactly the PRD section 11 contract.

    The RCA engine consumes only this object, so it never learns whether the
    data came from CSV, Excel or SQL Server.
    """
    definition = {
        "name": payload["name"],
        "column": payload["column"],
        "aggregation": payload["aggregation"],
        "time_column": payload.get("time_column"),
        "dimensions": list(payload.get("dimensions") or []),
        "comparison": payload["comparison"],
    }
    if payload.get("comparison_config"):
        definition["comparison_config"] = payload["comparison_config"]
    if payload.get("filters"):
        definition["filters"] = payload["filters"]
    return definition


def create_definition(
    db: Session,
    dataset: Dataset,
    payload: dict[str, Any],
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> tuple[KpiDefinition, Any]:
    profile_view = load_profile_view(db, dataset)
    definition = normalize_definition(payload)

    report = validate_for_kpi(profile_view, definition)
    if report.state == ValidationState.BLOCKED.value:
        raise ValidationError(
            "This KPI configuration cannot be analysed.",
            code="KPI_VALIDATION_BLOCKED",
            details={"state": report.state, "issues": [i.to_dict() for i in report.issues]},
        )

    # One active definition per dataset. Enforced here as well as by the
    # partial unique index, because that index is PostgreSQL-only.
    for existing in db.scalars(
        select(KpiDefinition).where(
            KpiDefinition.dataset_id == dataset.id, KpiDefinition.is_active.is_(True)
        )
    ).all():
        existing.is_active = False

    record = KpiDefinition(
        dataset_id=dataset.id,
        company_id=company_id,
        created_by=user_id,
        name=definition["name"],
        column_name=definition["column"],
        aggregation=definition["aggregation"],
        time_column=definition.get("time_column"),
        dimensions=definition["dimensions"],
        comparison=definition["comparison"],
        comparison_config=payload.get("comparison_config"),
        filters=payload.get("filters"),
        definition=definition,
        is_active=True,
        validation_state=report.state,
    )
    db.add(record)
    db.flush()

    db.add(
        SchemaValidation(
            dataset_id=dataset.id,
            kpi_definition_id=record.id,
            mode=ValidationMode.ANALYSIS.value,
            state=report.state,
            error_count=report.error_count,
            warning_count=report.warning_count,
            info_count=report.info_count,
            issues=[i.to_dict() for i in report.issues],
        )
    )

    # Configuring a KPI is what makes a dataset Analysis Ready (PRD section 13).
    if dataset.status != DatasetStatus.BLOCKED.value:
        dataset.status = DatasetStatus.ANALYSIS_READY.value
    db.commit()
    db.refresh(record)
    return record, report


def get_active_definition(db: Session, dataset: Dataset) -> KpiDefinition:
    record = db.scalar(
        select(KpiDefinition).where(
            KpiDefinition.dataset_id == dataset.id, KpiDefinition.is_active.is_(True)
        )
    )
    if record is None:
        raise NotFoundError("No KPI definition has been configured.", code="KPI_DEFINITION_NOT_FOUND")
    return record


def delete_definition(db: Session, dataset: Dataset, kpi_id: uuid.UUID) -> None:
    record = db.scalar(
        select(KpiDefinition).where(
            KpiDefinition.id == kpi_id, KpiDefinition.dataset_id == dataset.id
        )
    )
    if record is None:
        raise NotFoundError("KPI definition not found.", code="KPI_DEFINITION_NOT_FOUND")
    was_active = record.is_active
    db.delete(record)

    if was_active and dataset.status == DatasetStatus.ANALYSIS_READY.value:
        dataset.status = DatasetStatus.PROFILED.value
    db.commit()


def _maybe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
