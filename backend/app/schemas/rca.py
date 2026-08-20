"""Request and response contracts for root cause analysis.

Nothing here is ORM-backed, so no schema carries ``from_attributes``: the engine
returns frozen dataclasses and ``to_result`` maps them at the API boundary. That
mapping is what keeps Pydantic out of ``app.analysis``.

Wording is deliberate throughout: *driver*, *contributor*, *contribution*,
*offsetting factor*. The engine measures how much a segment contributed to a
measured change; it does not establish that the segment caused it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.analysis.rca.models import DriverNode, RcaResult


class InvestigationRequest(BaseModel):
    dataset_id: UUID
    # Defaults to the dataset's active definition.
    kpi_definition_id: UUID | None = None
    max_drivers: int = Field(default=5, ge=1, le=20)
    max_tree_depth: int = Field(default=3, ge=1, le=3)


class PeriodRead(BaseModel):
    """A half-open window. ``end`` is exclusive."""

    label: str
    start: datetime
    end: datetime
    row_count: int = 0


class PeriodsRead(BaseModel):
    current: PeriodRead
    previous: PeriodRead
    grain: str
    strategy: str
    anchor: datetime
    # Set when the newest period was still being collected and was left out.
    excluded_partial_period: PeriodRead | None = None


class KpiChangeRead(BaseModel):
    name: str
    column: str
    aggregation: str
    time_column: str | None = None
    current_value: float | None = None
    previous_value: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None
    percent_change_undefined_reason: str | None = None
    direction: str
    severity: str
    comparison: str
    grain: str


class AttributionRead(BaseModel):
    """How the contribution numbers were produced, or why there are none."""

    basis: str
    unattributable_reason: str | None = None
    change_pattern: str
    pareto_target: float
    min_material_contribution: float
    has_offsetting: bool
    additivity_verified: bool | None = None


class DriverRead(BaseModel):
    node_id: str
    depth: int
    path: list[dict[str, str | None]] = Field(default_factory=list)
    dimension: str | None = None
    value: str | None = None
    value_is_null: bool = False

    current_value: float | None = None
    previous_value: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None
    percent_change_undefined_reason: str | None = None

    # Share of the GLOBAL KPI change at every depth, so "58%" always means 58%
    # of the movement the user asked about.
    contribution: float | None = None
    contribution_basis: str
    unattributable_reason: str | None = None
    share_of_parent_change: float | None = None
    # AVG only, and level-local: a parent's rate absorbs mix internal to its
    # children, so these must not be summed across depths.
    rate_effect: float | None = None
    mix_effect: float | None = None

    current_count: int = 0
    previous_count: int = 0
    current_rows: int = 0
    previous_rows: int = 0
    current_share: float | None = None
    previous_share: float | None = None
    # What the change would have been at this segment's baseline share, and the
    # surprise on top - which is what separates "it shrank because everything
    # shrank" from "it shrank on its own".
    expected_change: float | None = None
    excess_change: float | None = None

    is_new_segment: bool = False
    is_lost_segment: bool = False
    low_support: bool = False
    support_reason: str | None = None
    is_other_bucket: bool = False
    is_pure_split: bool = False

    classification: str
    rank: int = 0
    child_dimension: str | None = None
    child_split_type: str | None = None
    child_explanatory_power: float | None = None
    unexplained_share: float | None = None
    stop_reason: str | None = None
    children: list["DriverRead"] = Field(default_factory=list)


class DimensionResultRead(BaseModel):
    dimension: str
    segments: list[DriverRead] = Field(default_factory=list)


class DimensionSummaryRead(BaseModel):
    dimension: str
    segment_count: int
    truncated: bool = False
    explanatory_power: float | None = None
    excluded_reason: str | None = None


class EvidenceRead(BaseModel):
    total_rows: int = 0
    current_rows: int = 0
    previous_rows: int = 0
    rows_outside_periods: int = 0
    unparsed_time_rows: int = 0
    unparsed_measure_rows: int = 0
    statements_executed: int = 0
    duration_ms: int = 0
    # Should be 1.0. Exposed so the invariant is observable in production
    # rather than only in tests.
    contribution_sum: float | None = None


class NoticeRead(BaseModel):
    code: str
    severity: str
    message: str
    details: dict[str, Any] | None = None


class InvestigationResult(BaseModel):
    dataset_id: UUID
    dataset_name: str
    kpi_definition_id: UUID
    generated_at: datetime
    state: str
    kpi: KpiChangeRead
    attribution: AttributionRead
    periods: PeriodsRead | None = None
    # Depth-1 nodes only: a flat cross-depth list would contain both "Cairo" and
    # "Cairo / Product A" at 100% and every consumer would double count.
    primary_drivers: list[DriverRead] = Field(default_factory=list)
    secondary_drivers: list[DriverRead] = Field(default_factory=list)
    offsetting_factors: list[DriverRead] = Field(default_factory=list)
    dimension_results: list[DimensionResultRead] = Field(default_factory=list)
    dimensions_analysed: list[DimensionSummaryRead] = Field(default_factory=list)
    rca_tree: DriverRead | None = None
    evidence: EvidenceRead
    notices: list[NoticeRead] = Field(default_factory=list)
    summary: str


DriverRead.model_rebuild()


def _driver(node: DriverNode) -> DriverRead:
    return DriverRead(
        node_id=node.node_id,
        depth=node.depth,
        path=[{"dimension": d, "value": v} for d, v in node.path],
        dimension=node.dimension,
        value=node.value,
        value_is_null=node.value_is_null,
        current_value=node.current_value,
        previous_value=node.previous_value,
        absolute_change=node.absolute_change,
        percent_change=node.percent_change,
        percent_change_undefined_reason=node.percent_change_undefined_reason,
        contribution=node.contribution,
        contribution_basis=node.contribution_basis.value,
        unattributable_reason=(
            node.unattributable_reason.value if node.unattributable_reason else None
        ),
        share_of_parent_change=node.share_of_parent_change,
        rate_effect=node.rate_effect,
        mix_effect=node.mix_effect,
        current_count=node.current_count,
        previous_count=node.previous_count,
        current_rows=node.current_rows,
        previous_rows=node.previous_rows,
        current_share=node.current_share,
        previous_share=node.previous_share,
        expected_change=node.expected_change,
        excess_change=node.excess_change,
        is_new_segment=node.is_new_segment,
        is_lost_segment=node.is_lost_segment,
        low_support=node.low_support,
        support_reason=node.support_reason,
        is_other_bucket=node.is_other_bucket,
        is_pure_split=node.is_pure_split,
        classification=node.classification.value,
        rank=node.rank,
        child_dimension=node.child_dimension,
        child_split_type=node.child_split_type,
        child_explanatory_power=node.child_explanatory_power,
        unexplained_share=node.unexplained_share,
        stop_reason=node.stop_reason,
        children=[_driver(child) for child in node.children],
    )


def _period(period, label: str | None = None) -> PeriodRead:
    return PeriodRead(
        label=label or period.label,
        start=period.start,
        end=period.end,
        row_count=period.row_count,
    )


def to_result(
    *,
    dataset_id: UUID,
    dataset_name: str,
    kpi_definition_id: UUID,
    generated_at: datetime,
    result: RcaResult,
) -> InvestigationResult:
    """Map the engine's dataclasses onto the wire contract."""
    periods = None
    if result.periods is not None:
        periods = PeriodsRead(
            current=_period(result.periods.current),
            previous=_period(result.periods.previous),
            grain=result.periods.grain.value,
            strategy=result.periods.strategy,
            anchor=result.periods.anchor,
            excluded_partial_period=(
                _period(result.periods.excluded_partial_period, "excluded")
                if result.periods.excluded_partial_period is not None
                else None
            ),
        )

    kpi = result.kpi
    attribution = result.attribution

    return InvestigationResult(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        kpi_definition_id=kpi_definition_id,
        generated_at=generated_at,
        state=result.state.value,
        kpi=KpiChangeRead(
            name=kpi.name,
            column=kpi.column,
            aggregation=kpi.aggregation,
            time_column=kpi.time_column,
            current_value=kpi.current_value,
            previous_value=kpi.previous_value,
            absolute_change=kpi.absolute_change,
            percent_change=kpi.percent_change,
            percent_change_undefined_reason=kpi.percent_change_undefined_reason,
            direction=kpi.direction,
            severity=kpi.severity,
            comparison=kpi.comparison,
            grain=kpi.grain,
        ),
        attribution=AttributionRead(
            basis=attribution.basis.value,
            unattributable_reason=(
                attribution.unattributable_reason.value
                if attribution.unattributable_reason
                else None
            ),
            change_pattern=attribution.change_pattern.value,
            pareto_target=attribution.pareto_target,
            min_material_contribution=attribution.min_material_contribution,
            has_offsetting=attribution.has_offsetting,
            additivity_verified=attribution.additivity_verified,
        ),
        periods=periods,
        primary_drivers=[_driver(n) for n in result.primary_drivers],
        secondary_drivers=[_driver(n) for n in result.secondary_drivers],
        offsetting_factors=[_driver(n) for n in result.offsetting_factors],
        dimension_results=[
            DimensionResultRead(dimension=name, segments=[_driver(n) for n in nodes])
            for name, nodes in result.dimension_results
        ],
        dimensions_analysed=[
            DimensionSummaryRead(
                dimension=s.dimension,
                segment_count=s.segment_count,
                truncated=s.truncated,
                explanatory_power=s.explanatory_power,
                excluded_reason=s.excluded_reason,
            )
            for s in result.dimensions_analysed
        ],
        rca_tree=_driver(result.tree) if result.tree is not None else None,
        evidence=EvidenceRead(
            total_rows=result.evidence.total_rows,
            current_rows=result.evidence.current_rows,
            previous_rows=result.evidence.previous_rows,
            rows_outside_periods=result.evidence.rows_outside_periods,
            unparsed_time_rows=result.evidence.unparsed_time_rows,
            unparsed_measure_rows=result.evidence.unparsed_measure_rows,
            statements_executed=result.evidence.statements_executed,
            duration_ms=result.evidence.duration_ms,
            contribution_sum=result.evidence.contribution_sum,
        ),
        notices=[
            NoticeRead(
                code=n.code, severity=n.severity, message=n.message, details=n.details
            )
            for n in result.notices
        ],
        summary=result.summary,
    )
