"""Orchestration for root cause analysis.

Resolves the dataset, its KPI definition and the profiled reporting frequency,
then hands a plain spec to the pure engine. This is the only layer that touches
the database or storage; ``app.analysis.rca`` never imports either.

Nothing is persisted - an investigation is recomputed on request.
"""

import uuid

from sqlalchemy.orm import Session

from app.analysis.rca import run_investigation, verify_tree
from app.analysis.rca.constants import MAX_PRIMARY_DRIVERS, MAX_TREE_DEPTH
from app.analysis.rca.models import RcaResult, RcaSpec
from app.core.config import settings
from app.core.exceptions import NotReadyError, ValidationError
from app.core.logging import get_logger
from app.db.models.dataset import Dataset
from app.db.models.enums import Aggregation, ComparisonPeriod, DatasetStatus
from app.db.models.kpi import KpiDefinition
from app.services import dataset_service, kpi_service
from app.services.dataset_source import open_dataset_relation

logger = get_logger(__name__)


def build_spec(
    definition: KpiDefinition,
    frequency: str | None,
    *,
    max_drivers: int,
    max_tree_depth: int,
) -> RcaSpec:
    """Translate the stored definition into the engine's input.

    The aggregation and comparison strings are re-parsed through their enums
    here: they are stored values, and an unrecognised one must fail before it
    can be looked up in a SQL-function map.
    """
    contract = definition.definition or {}

    try:
        aggregation = Aggregation(contract.get("aggregation", definition.aggregation))
    except ValueError as exc:
        raise ValidationError(
            f"{definition.aggregation!r} is not a supported aggregation.",
            code="RCA_AGGREGATION_UNSUPPORTED",
            details={"aggregation": definition.aggregation},
        ) from exc

    try:
        comparison = ComparisonPeriod(contract.get("comparison", definition.comparison))
    except ValueError as exc:
        raise ValidationError(
            f"{definition.comparison!r} is not a supported comparison period.",
            code="RCA_COMPARISON_UNSUPPORTED",
            details={"comparison": definition.comparison},
        ) from exc

    time_column = contract.get("time_column", definition.time_column)
    if not time_column:
        raise ValidationError(
            "This KPI has no time column, so there is no period-over-period change to explain. "
            "Add one in KPI setup.",
            code="KPI_TIME_COLUMN_REQUIRED",
        )

    filters = contract.get("filters") or definition.filters or []

    return RcaSpec(
        kpi_name=definition.name,
        measure_column=contract.get("column", definition.column_name),
        aggregation=aggregation,
        time_column=time_column,
        dimensions=tuple(contract.get("dimensions") or definition.dimensions or []),
        comparison=comparison,
        comparison_config=contract.get("comparison_config") or definition.comparison_config,
        filters=tuple(filters),
        detected_frequency=frequency,
        max_drivers=max_drivers,
        max_tree_depth=max_tree_depth,
        max_values_per_dimension=settings.rca_max_values_per_dimension,
        max_segments_scanned=settings.rca_max_segments_scanned,
    )


def run(
    db: Session,
    dataset_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    kpi_definition_id: uuid.UUID | None = None,
    max_drivers: int = MAX_PRIMARY_DRIVERS,
    max_tree_depth: int = MAX_TREE_DEPTH,
) -> tuple[Dataset, KpiDefinition, RcaResult]:
    """Run an investigation and return it with the rows it was built from."""
    dataset = dataset_service.get_dataset(db, dataset_id, company_id)

    if dataset.status != DatasetStatus.ANALYSIS_READY.value:
        raise NotReadyError(
            "This dataset is not ready for analysis yet. Configure a KPI to make it "
            "Analysis Ready.",
            code="DATASET_NOT_ANALYSIS_READY",
            details={"status": dataset.status},
        )

    definition = kpi_service.resolve_definition(db, dataset, kpi_definition_id)
    frequency = kpi_service.detected_frequency(db, dataset, definition.time_column)
    spec = build_spec(
        definition, frequency, max_drivers=max_drivers, max_tree_depth=max_tree_depth
    )

    with open_dataset_relation(dataset) as (conn, relation):
        result = run_investigation(conn, relation, spec)

    if result.tree is not None:
        verify_tree(result.tree)

    logger.info(
        "rca_investigation",
        extra={
            "dataset_id": str(dataset.id),
            "kpi_definition_id": str(definition.id),
            "state": result.state.value,
            "statements": result.evidence.statements_executed,
            "duration_ms": result.evidence.duration_ms,
        },
    )
    return dataset, definition, result
