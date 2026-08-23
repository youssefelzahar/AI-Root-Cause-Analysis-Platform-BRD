"""Value types for the RCA engine.

Frozen dataclasses rather than Pydantic: this package is pure analysis and must
stay importable without the web layer, exactly like ``app.analysis.profiler``.
The route module maps these to schemas at the API boundary.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.db.models.enums import Aggregation, ComparisonPeriod


class Grain(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    # Used when no reporting frequency could be detected: the observed range is
    # halved instead of a calendar bucket being invented.
    EQUAL_SPAN = "equal_span"
    CUSTOM = "custom"


class AttributionBasis(str, Enum):
    """How a contribution number was arrived at - or why there isn't one."""

    NET_CHANGE = "net_change"  # c_i = delta_i / delta_total   (SUM, COUNT)
    MIX_RATE = "mix_rate"  # c_i = (rate_i + mix_i) / dA   (AVG)
    GROSS_MOVEMENT = "gross_movement"  # c_i = delta_i / sum|delta_j|
    UNATTRIBUTABLE = "unattributable"  # contribution is None


class UnattributableReason(str, Enum):
    NON_ADDITIVE_SET_OVERLAP = "non_additive_set_overlap"  # COUNT_DISTINCT
    ORDER_STATISTIC = "order_statistic"  # MIN, MAX
    DISTRIBUTIONAL_STATISTIC = "distributional_statistic"  # MEDIAN
    NO_PREVIOUS_PERIOD = "no_previous_period"
    NO_TIME_COLUMN = "no_time_column"


class AnalysisState(str, Enum):
    OK = "ok"
    NO_DATA = "no_data"
    NO_PREVIOUS_PERIOD = "no_previous_period"
    NO_CHANGE = "no_change"
    NO_TIME_COLUMN = "no_time_column"
    UNATTRIBUTABLE = "unattributable"


class ChangePattern(str, Enum):
    SINGLE_DRIVER = "single_driver"
    CONCENTRATED = "concentrated"
    # Every cell moved in proportion to its baseline share, so no dimension
    # explains the change and naming drivers would be an invention.
    BROAD_BASED = "broad_based"
    OFFSETTING = "offsetting"
    NONE = "none"


class Classification(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    OFFSETTING = "offsetting"
    IMMATERIAL = "immaterial"
    RESIDUAL = "residual"  # the (other) truncation bucket


# Aggregations whose group value is a mean-like statistic: unstable on small
# groups, so they get a support gate that additive aggregations must not have.
MEAN_LIKE = frozenset({Aggregation.AVG, Aggregation.MEDIAN})

# Aggregations that decompose additively over disjoint groups.
ADDITIVE = frozenset({Aggregation.SUM, Aggregation.COUNT})


@dataclass(frozen=True)
class RcaSpec:
    """Everything the engine needs, resolved from the KPI definition."""

    kpi_name: str
    measure_column: str
    aggregation: Aggregation
    time_column: str | None
    dimensions: tuple[str, ...]
    comparison: ComparisonPeriod
    comparison_config: dict[str, Any] | None = None
    filters: tuple[dict[str, Any], ...] = ()
    detected_frequency: str | None = None
    max_drivers: int = 5
    max_tree_depth: int = 3
    max_values_per_dimension: int = 50
    max_segments_scanned: int = 5_000

    @property
    def segment_limit(self) -> int:
        """The one limit governing the query, the truncation test and drill levels.

        Both caps have to resolve to a single number or truncation goes undetected:
        clamping the query to the lower of the two while testing ``truncated``
        against the higher one drops segments with no residual bucket and no
        notice, which silently breaks the contribution sum.
        """
        return min(self.max_values_per_dimension, self.max_segments_scanned)


@dataclass(frozen=True)
class Period:
    """A half-open window ``[start, end)``.

    Half-open throughout: ``t <= '2026-07-31'`` silently drops every row at
    ``2026-07-31 09:15`` once the column is a TIMESTAMP.
    """

    label: str
    start: datetime
    end: datetime
    row_count: int = 0
    distinct_days: int = 0


@dataclass(frozen=True)
class PeriodResolution:
    current: Period
    previous: Period
    grain: Grain
    strategy: str
    anchor: datetime
    excluded_partial_period: Period | None = None


@dataclass(frozen=True)
class SegmentTotals:
    """One (dimension, segment) row with both periods - the contract with the maths.

    ``value`` is None only when the segment carries a SQL NULL for that
    dimension; it is kept as a real group rather than folded into a sentinel
    string, because every row must belong to exactly one cell for the
    decomposition to be valid.
    """

    dimension: str
    value: str | None
    value_is_null: bool
    current_value: float | None
    previous_value: float | None
    current_count: int  # non-null measure values -> AVG weights
    previous_count: int
    current_rows: int  # all rows -> new/lost detection and support
    previous_rows: int

    @property
    def is_new(self) -> bool:
        return self.previous_rows == 0 and self.current_rows > 0

    @property
    def is_lost(self) -> bool:
        return self.current_rows == 0 and self.previous_rows > 0

    @property
    def total_rows(self) -> int:
        return self.current_rows + self.previous_rows


@dataclass(frozen=True)
class Totals:
    """Level totals. For the root these are the global totals."""

    current_value: float | None
    previous_value: float | None
    current_count: int
    previous_count: int
    current_rows: int
    previous_rows: int

    @property
    def absolute_change(self) -> float | None:
        if self.current_value is None and self.previous_value is None:
            return None
        return (self.current_value or 0.0) - (self.previous_value or 0.0)


@dataclass
class DriverNode:
    """A node in the RCA tree.

    ``contribution`` is always a share of the GLOBAL total change, at every
    depth, so "58%" always means 58% of the KPI movement the user asked about.
    ``share_of_parent_change`` carries the local view under its own name, so no
    single field's meaning depends on depth.
    """

    node_id: str
    depth: int
    path: tuple[tuple[str, str | None], ...]
    dimension: str | None
    value: str | None
    value_is_null: bool = False

    current_value: float | None = None
    previous_value: float | None = None
    absolute_change: float | None = None
    percent_change: float | None = None
    percent_change_undefined_reason: str | None = None

    contribution: float | None = None
    contribution_basis: AttributionBasis = AttributionBasis.NET_CHANGE
    unattributable_reason: UnattributableReason | None = None
    share_of_parent_change: float | None = None
    # AVG only, and LEVEL-LOCAL: a parent's rate absorbs mix internal to its
    # children, so these must never be summed across depths.
    rate_effect: float | None = None
    mix_effect: float | None = None

    current_count: int = 0
    previous_count: int = 0
    current_rows: int = 0
    previous_rows: int = 0
    current_share: float | None = None
    previous_share: float | None = None
    # What the change would have been had this segment moved in proportion to
    # its baseline share, and the surprise on top. Without these a large
    # segment always looks like the driver simply because it is large.
    expected_change: float | None = None
    excess_change: float | None = None

    is_new_segment: bool = False
    is_lost_segment: bool = False
    low_support: bool = False
    support_reason: str | None = None
    is_other_bucket: bool = False
    is_pure_split: bool = False

    classification: Classification = Classification.IMMATERIAL
    rank: int = 0
    child_dimension: str | None = None
    child_split_type: str | None = None
    child_explanatory_power: float | None = None
    unexplained_share: float | None = None
    stop_reason: str | None = None
    children: list["DriverNode"] = field(default_factory=list)


@dataclass(frozen=True)
class DimensionSummary:
    dimension: str
    segment_count: int
    truncated: bool = False
    explanatory_power: float | None = None
    excluded_reason: str | None = None


@dataclass(frozen=True)
class Notice:
    """Something the user must know that is not an error."""

    code: str
    severity: str  # "info" | "warning"
    message: str
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class Evidence:
    total_rows: int = 0
    current_rows: int = 0
    previous_rows: int = 0
    rows_outside_periods: int = 0
    unparsed_time_rows: int = 0
    unparsed_measure_rows: int = 0
    statements_executed: int = 0
    duration_ms: int = 0
    contribution_sum: float | None = None


@dataclass(frozen=True)
class KpiChange:
    name: str
    column: str
    aggregation: str
    time_column: str | None
    current_value: float | None
    previous_value: float | None
    absolute_change: float | None
    percent_change: float | None
    percent_change_undefined_reason: str | None
    direction: str  # up | down | flat | unknown
    severity: str
    comparison: str
    grain: str


@dataclass(frozen=True)
class Attribution:
    basis: AttributionBasis
    unattributable_reason: UnattributableReason | None
    change_pattern: ChangePattern
    pareto_target: float
    min_material_contribution: float
    has_offsetting: bool
    additivity_verified: bool | None = None


@dataclass(frozen=True)
class RcaResult:
    state: AnalysisState
    kpi: KpiChange
    attribution: Attribution
    periods: PeriodResolution | None
    primary_drivers: tuple[DriverNode, ...] = ()
    secondary_drivers: tuple[DriverNode, ...] = ()
    offsetting_factors: tuple[DriverNode, ...] = ()
    dimension_results: tuple[tuple[str, tuple[DriverNode, ...]], ...] = ()
    dimensions_analysed: tuple[DimensionSummary, ...] = ()
    tree: DriverNode | None = None
    evidence: Evidence = field(default_factory=Evidence)
    notices: tuple[Notice, ...] = ()
    summary: str = ""
