"""Orchestration for anomaly detection.

Resolves the dataset, its KPI definition and the profiled reporting frequency,
then hands a plain spec to the pure engine. This is the only layer that touches
the database or storage; ``app.analysis.anomaly`` never imports either.

Nothing is persisted - a detection is recomputed on request, exactly like an
investigation.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.anomaly import detect_anomalies
from app.analysis.anomaly.constants import (
    BASELINE_WINDOW,
    DEFAULT_GRAIN,
    MIN_BASELINE_OBSERVATIONS,
)
from app.analysis.anomaly.detectors import DEFAULT_METHOD, DETECTORS
from app.analysis.anomaly.models import AnomalyReport, AnomalySpec
from app.analysis.anomaly.series import grain_for
from app.analysis.rca.models import Grain
from app.core.config import settings
from app.core.exceptions import NotFoundError, NotReadyError, ValidationError
from app.core.logging import get_logger
from app.db.models.dataset import Dataset
from app.db.models.enums import Aggregation, DatasetStatus
from app.db.models.kpi import KpiDefinition
from app.db.models.profile import ColumnProfile, DatasetProfile
from app.services import dataset_service, kpi_service
from app.services.dataset_source import open_dataset_relation

logger = get_logger(__name__)


def _resolve_definition(
    db: Session, dataset: Dataset, kpi_definition_id: uuid.UUID | None
) -> KpiDefinition:
    """The definition to analyse: an explicit one, or the dataset's active one.

    An id belonging to a different dataset returns 404 rather than leaking that
    it exists.
    """
    if kpi_definition_id is None:
        return kpi_service.get_active_definition(db, dataset)

    record = db.scalar(
        select(KpiDefinition).where(
            KpiDefinition.id == kpi_definition_id,
            KpiDefinition.dataset_id == dataset.id,
        )
    )
    if record is None:
        raise NotFoundError(
            "That KPI definition does not exist for this dataset.",
            code="KPI_DEFINITION_NOT_FOUND",
        )
    return record


def _detected_frequency(db: Session, dataset: Dataset, time_column: str | None) -> str | None:
    """The profiler's reporting-frequency finding for the KPI's time column.

    Already null below 0.6 confidence, so an absent value genuinely means "we
    could not tell", and the engine says so rather than inventing a grain.
    """
    if not time_column:
        return None
    profile = db.scalar(select(DatasetProfile).where(DatasetProfile.dataset_id == dataset.id))
    if profile is None:
        return None
    column = db.scalar(
        select(ColumnProfile).where(
            ColumnProfile.dataset_profile_id == profile.id,
            ColumnProfile.column_name == time_column,
        )
    )
    if column is None:
        return None
    return (column.datetime_stats or {}).get("detected_frequency")


def _resolve_grain(requested: str | None, detected: str | None) -> tuple[Grain, bool]:
    """The grain to build the series at, and whether it had to be assumed.

    An explicit request wins. The profiler's frequency is the rows' *arrival*
    cadence - for transactional data that is "daily" no matter what the business
    considers a reporting period - so overriding it is a first-class control,
    not an escape hatch.
    """
    if requested is not None:
        try:
            grain = Grain(requested)
        except ValueError as exc:
            raise ValidationError(
                f"{requested!r} is not a supported reporting grain.",
                code="ANOMALY_GRAIN_UNSUPPORTED",
                details={"grain": requested, "supported": [g.value for g in _SERIES_GRAINS]},
            ) from exc
        resolved = grain_for(detected, grain)
        if resolved is None:
            raise ValidationError(
                f"{requested!r} is not a grain a continuous series can be built on.",
                code="ANOMALY_GRAIN_UNSUPPORTED",
                details={"grain": requested, "supported": [g.value for g in _SERIES_GRAINS]},
            )
        return resolved, False

    resolved = grain_for(detected, None)
    if resolved is not None:
        return resolved, False
    return Grain(DEFAULT_GRAIN), True


_SERIES_GRAINS = (Grain.DAY, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR)


def _build_spec(
    definition: KpiDefinition,
    *,
    grain: Grain,
    grain_assumed: bool,
    detected_frequency: str | None,
    method: str,
    baseline_window: int,
) -> AnomalySpec:
    """Translate the stored definition into the engine's input.

    The aggregation is re-parsed through its enum here: it is a stored value,
    and an unrecognised one must fail before it can be looked up in a
    SQL-function map.
    """
    contract = definition.definition or {}

    try:
        aggregation = Aggregation(contract.get("aggregation", definition.aggregation))
    except ValueError as exc:
        raise ValidationError(
            f"{definition.aggregation!r} is not a supported aggregation.",
            code="ANOMALY_AGGREGATION_UNSUPPORTED",
            details={"aggregation": definition.aggregation},
        ) from exc

    if method not in DETECTORS:
        raise ValidationError(
            f"{method!r} is not a supported detection method.",
            code="ANOMALY_METHOD_UNSUPPORTED",
            details={"method": method, "supported": sorted(DETECTORS)},
        )

    time_column = contract.get("time_column", definition.time_column)
    if not time_column:
        raise ValidationError(
            "This KPI has no time column, so it has no history to compare against. "
            "Add one in KPI setup.",
            code="KPI_TIME_COLUMN_REQUIRED",
        )

    filters = contract.get("filters") or definition.filters or []

    return AnomalySpec(
        kpi_name=definition.name,
        measure_column=contract.get("column", definition.column_name),
        aggregation=aggregation,
        time_column=time_column,
        grain=grain,
        # Dimensions are deliberately ignored: this is a whole-dataset series.
        # Attributing a movement across dimensions is the RCA engine's job.
        filters=tuple(filters),
        method=method,
        baseline_window=baseline_window,
        min_baseline_observations=MIN_BASELINE_OBSERVATIONS,
        max_periods=settings.anomaly_max_periods,
        detected_frequency=detected_frequency,
        grain_assumed=grain_assumed,
    )


def run(
    db: Session,
    dataset_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    kpi_definition_id: uuid.UUID | None = None,
    grain: str | None = None,
    method: str = DEFAULT_METHOD,
    baseline_window: int = BASELINE_WINDOW,
) -> tuple[Dataset, KpiDefinition, AnomalyReport]:
    """Detect anomalies and return the report with the rows it was built from."""
    dataset = dataset_service.get_dataset(db, dataset_id, company_id)

    if dataset.status != DatasetStatus.ANALYSIS_READY.value:
        raise NotReadyError(
            "This dataset is not ready for analysis yet. Configure a KPI to make it "
            "Analysis Ready.",
            code="DATASET_NOT_ANALYSIS_READY",
            details={"status": dataset.status},
        )

    definition = _resolve_definition(db, dataset, kpi_definition_id)
    detected = _detected_frequency(db, dataset, definition.time_column)
    resolved_grain, assumed = _resolve_grain(grain, detected)
    spec = _build_spec(
        definition,
        grain=resolved_grain,
        grain_assumed=assumed,
        detected_frequency=detected,
        method=method,
        baseline_window=baseline_window,
    )

    with open_dataset_relation(dataset) as (conn, relation):
        report = detect_anomalies(conn, relation, spec)

    logger.info(
        "anomaly_detection",
        extra={
            "dataset_id": str(dataset.id),
            "kpi_definition_id": str(definition.id),
            "status": report.status.value,
            "grain": report.grain.value,
            "method": report.method,
            "periods": report.evidence.periods_observed,
            "anomalies": len(report.anomalies),
            "statements": report.evidence.statements_executed,
            "duration_ms": report.evidence.duration_ms,
        },
    )
    return dataset, definition, report
