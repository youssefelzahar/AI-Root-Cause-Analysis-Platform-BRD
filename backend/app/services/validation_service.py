"""Schema validation (PRD section 10) producing PASS / WARNING / BLOCKED.

Thresholds are constants so the rules are pinned by tests rather than by
whatever the implementation happened to do.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from app.analysis.type_inference import (
    MIN_ACCEPTABLE_CONFIDENCE,
    STRONG_CONFIDENCE,
    confidence_label,
)
from app.db.models.enums import (
    NUMERIC_AGGREGATIONS,
    Aggregation,
    IssueSeverity,
    ValidationState,
)

# --- thresholds ----------------------------------------------------------
MIN_ROWS_ERROR = 2
WEAK_SAMPLE_ROWS = 30
COLUMN_HIGH_NULL_PCT = 50.0
DATASET_HIGH_MISSING_PCT = 40.0
DATASET_CRITICAL_MISSING_PCT = 70.0
HIGH_DUPLICATE_PCT = 30.0
ALL_DUPLICATE_PCT = 99.0
HIGH_CARDINALITY_DIMENSION = 1000
KPI_MEASURE_MAX_NULL_PCT = 20.0
MIN_PERIODS_ERROR = 2
SPARSE_PERIODS = 6
MAX_DIMENSIONS = 5


@dataclass
class ValidationIssue:
    code: str
    severity: str
    message: str
    column: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    suggested_fix: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    state: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.ERROR.value)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.WARNING.value)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == IssueSeverity.INFO.value)


def _state_from(issues: list[ValidationIssue]) -> str:
    if any(i.severity == IssueSeverity.ERROR.value for i in issues):
        return ValidationState.BLOCKED.value
    if any(i.severity == IssueSeverity.WARNING.value for i in issues):
        return ValidationState.WARNING.value
    return ValidationState.PASS.value


def validate_structural(profile: Any) -> ValidationReport:
    """Is this dataset structurally usable for analysis at all?

    ``profile`` is an ``app.analysis.profiler.ProfileResult``.
    """
    issues: list[ValidationIssue] = []

    if profile.column_count == 0:
        issues.append(
            ValidationIssue("NO_COLUMNS", IssueSeverity.ERROR.value, "The file has no columns.")
        )
        return ValidationReport(_state_from(issues), issues)

    if profile.row_count == 0:
        issues.append(ValidationIssue("NO_ROWS", IssueSeverity.ERROR.value, "The file has no data rows."))
        return ValidationReport(_state_from(issues), issues)

    if profile.row_count < MIN_ROWS_ERROR:
        issues.append(
            ValidationIssue(
                "TOO_FEW_ROWS",
                IssueSeverity.ERROR.value,
                f"Only {profile.row_count} row(s); at least {MIN_ROWS_ERROR} are needed to compare periods.",
            )
        )
    elif profile.row_count < WEAK_SAMPLE_ROWS:
        issues.append(
            ValidationIssue(
                "WEAK_SAMPLE",
                IssueSeverity.WARNING.value,
                f"Only {profile.row_count} rows. Statistical results will be unreliable.",
            )
        )

    # Duplicate headers make column references ambiguous.
    seen: dict[str, str] = {}
    for column in profile.columns:
        key = column.name.strip().lower()
        if key in seen:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_COLUMN_NAMES",
                    IssueSeverity.ERROR.value,
                    f"Column name '{column.name}' appears more than once.",
                    column=column.name,
                )
            )
        seen[key] = column.name
        if not column.name.strip() or column.name.lower().startswith("unnamed"):
            issues.append(
                ValidationIssue(
                    "UNNAMED_COLUMN",
                    IssueSeverity.WARNING.value,
                    f"Column {column.ordinal} has no header name.",
                    column=column.name,
                )
            )

    numeric_columns = [c for c in profile.columns if c.inference.is_numeric]
    temporal_columns = [c for c in profile.columns if c.inference.is_temporal]

    if not numeric_columns:
        issues.append(
            ValidationIssue(
                "NO_NUMERIC_COLUMN",
                IssueSeverity.ERROR.value,
                "No numeric or numeric-convertible column was found, so no KPI can be measured.",
            )
        )
    if not temporal_columns:
        # Dimension-only analysis is still possible, so this warns.
        issues.append(
            ValidationIssue(
                "NO_TIME_COLUMN",
                IssueSeverity.WARNING.value,
                "No date column was found. Period-over-period comparison will not be available.",
            )
        )

    for column in profile.columns:
        inference = column.inference
        if column.null_pct >= 100:
            issues.append(
                ValidationIssue(
                    "COLUMN_FULLY_NULL",
                    IssueSeverity.WARNING.value,
                    f"Column '{column.name}' is entirely empty.",
                    column=column.name,
                )
            )
        elif column.null_pct >= COLUMN_HIGH_NULL_PCT:
            issues.append(
                ValidationIssue(
                    "COLUMN_HIGH_NULL",
                    IssueSeverity.WARNING.value,
                    f"Column '{column.name}' is {column.null_pct:.1f}% empty.",
                    column=column.name,
                    details={"null_pct": column.null_pct},
                )
            )

        # The PRD's headline example: revenue arrives as text but converts.
        lossy = MIN_ACCEPTABLE_CONFIDENCE <= inference.confidence < STRONG_CONFIDENCE
        if inference.requires_conversion and lossy:
            issues.append(
                ValidationIssue(
                    "LOSSY_TYPE_CONVERSION",
                    IssueSeverity.WARNING.value,
                    (
                        f"Column '{column.name}' was detected as text but converts to "
                        f"{inference.inferred_type.value} with {confidence_label(inference.confidence)} "
                        f"confidence. {inference.invalid_value_count} value(s) could not be converted."
                    ),
                    column=column.name,
                    details={
                        "detected_type": inference.raw_type,
                        "target_type": inference.inferred_type.value,
                        "conversion_confidence": inference.confidence,
                        "conversion_confidence_label": confidence_label(inference.confidence),
                        "invalid_value_count": inference.invalid_value_count,
                        "sample_invalid_values": inference.sample_invalid_values,
                    },
                    suggested_fix={"action": "convert", "target_type": inference.inferred_type.value},
                )
            )
        elif inference.requires_conversion and inference.confidence >= STRONG_CONFIDENCE:
            issues.append(
                ValidationIssue(
                    "TYPE_CONVERTED",
                    IssueSeverity.INFO.value,
                    (
                        f"Column '{column.name}' was stored as text and converted to "
                        f"{inference.inferred_type.value} with high confidence."
                    ),
                    column=column.name,
                    details={"conversion_confidence": inference.confidence},
                )
            )

        if (
            column.unique_count
            and column.unique_count > HIGH_CARDINALITY_DIMENSION
            and not inference.is_numeric
            and not inference.is_temporal
        ):
            issues.append(
                ValidationIssue(
                    "HIGH_CARDINALITY_DIMENSION",
                    IssueSeverity.WARNING.value,
                    (
                        f"Column '{column.name}' has {column.unique_count} distinct values. "
                        "Drill-down results will be truncated to the top contributors."
                    ),
                    column=column.name,
                    details={"unique_count": column.unique_count},
                )
            )

    if profile.missing_cell_pct > DATASET_CRITICAL_MISSING_PCT:
        issues.append(
            ValidationIssue(
                "DATASET_CRITICAL_MISSING",
                IssueSeverity.ERROR.value,
                f"{profile.missing_cell_pct:.1f}% of all cells are empty.",
            )
        )
    elif profile.missing_cell_pct > DATASET_HIGH_MISSING_PCT:
        issues.append(
            ValidationIssue(
                "DATASET_HIGH_MISSING",
                IssueSeverity.WARNING.value,
                f"{profile.missing_cell_pct:.1f}% of all cells are empty.",
            )
        )

    if profile.duplicate_row_pct is not None:
        if profile.duplicate_row_pct > ALL_DUPLICATE_PCT:
            issues.append(
                ValidationIssue(
                    "ALL_ROWS_DUPLICATE",
                    IssueSeverity.ERROR.value,
                    "Effectively every row is a duplicate.",
                )
            )
        elif profile.duplicate_row_pct > HIGH_DUPLICATE_PCT:
            issues.append(
                ValidationIssue(
                    "HIGH_DUPLICATE_ROWS",
                    IssueSeverity.WARNING.value,
                    f"{profile.duplicate_row_pct:.1f}% of rows are exact duplicates.",
                    details={"duplicate_row_pct": profile.duplicate_row_pct},
                )
            )

    return ValidationReport(_state_from(issues), issues)


def validate_for_kpi(profile: Any, definition: dict[str, Any]) -> ValidationReport:
    """Are the fields this analysis needs actually usable? (PRD section 10)"""
    issues: list[ValidationIssue] = []
    columns = {c.name: c for c in profile.columns}

    measure_name = definition.get("column")
    aggregation = definition.get("aggregation")
    time_column = definition.get("time_column")
    dimensions = definition.get("dimensions") or []

    # --- measure ---------------------------------------------------------
    measure = columns.get(measure_name)
    if measure is None:
        issues.append(
            ValidationIssue(
                "KPI_COLUMN_MISSING",
                IssueSeverity.ERROR.value,
                f"KPI column '{measure_name}' does not exist in this dataset.",
                column=measure_name,
            )
        )
    else:
        needs_numeric = aggregation in {a.value for a in NUMERIC_AGGREGATIONS}
        if needs_numeric and not measure.inference.is_numeric:
            issues.append(
                ValidationIssue(
                    "KPI_COLUMN_NOT_NUMERIC",
                    IssueSeverity.ERROR.value,
                    (
                        f"'{measure_name}' is {measure.inference.inferred_type.value}, which cannot be "
                        f"aggregated with {aggregation}. Use COUNT or COUNT_DISTINCT instead."
                    ),
                    column=measure_name,
                    suggested_fix={"action": "change_aggregation", "target": Aggregation.COUNT.value},
                )
            )
        elif needs_numeric and measure.inference.confidence < STRONG_CONFIDENCE:
            issues.append(
                ValidationIssue(
                    "KPI_COLUMN_LOSSY",
                    IssueSeverity.WARNING.value,
                    (
                        f"'{measure_name}' converts to a number with "
                        f"{confidence_label(measure.inference.confidence)} confidence; "
                        f"{measure.inference.invalid_value_count} value(s) will be ignored."
                    ),
                    column=measure_name,
                    details={"conversion_confidence": measure.inference.confidence},
                )
            )
        if measure.null_pct > KPI_MEASURE_MAX_NULL_PCT:
            issues.append(
                ValidationIssue(
                    "KPI_COLUMN_HIGH_NULL",
                    IssueSeverity.ERROR.value,
                    f"'{measure_name}' is {measure.null_pct:.1f}% empty, too sparse to measure reliably.",
                    column=measure_name,
                    details={"null_pct": measure.null_pct},
                )
            )

    # --- time column -----------------------------------------------------
    comparison = definition.get("comparison")
    if not time_column:
        if comparison:
            issues.append(
                ValidationIssue(
                    "TIME_COLUMN_NOT_SELECTED",
                    IssueSeverity.WARNING.value,
                    "No time column was selected. Period-over-period comparison will be skipped.",
                )
            )
    else:
        time_col = columns.get(time_column)
        if time_col is None:
            issues.append(
                ValidationIssue(
                    "TIME_COLUMN_MISSING",
                    IssueSeverity.ERROR.value,
                    f"Time column '{time_column}' does not exist in this dataset.",
                    column=time_column,
                )
            )
        elif not time_col.inference.is_temporal or time_col.inference.confidence < 0.90:
            issues.append(
                ValidationIssue(
                    "TIME_COLUMN_NOT_TEMPORAL",
                    IssueSeverity.ERROR.value,
                    f"'{time_column}' could not be read reliably as a date.",
                    column=time_column,
                )
            )
        else:
            periods = (time_col.datetime_stats or {}).get("distinct_periods") or 0
            if periods < MIN_PERIODS_ERROR:
                issues.append(
                    ValidationIssue(
                        "INSUFFICIENT_PERIODS",
                        IssueSeverity.ERROR.value,
                        f"'{time_column}' contains only {periods} period(s); at least 2 are needed.",
                        column=time_column,
                    )
                )
            elif periods < SPARSE_PERIODS:
                issues.append(
                    ValidationIssue(
                        "SPARSE_PERIODS",
                        IssueSeverity.WARNING.value,
                        f"'{time_column}' contains only {periods} periods. Trends will be weak.",
                        column=time_column,
                    )
                )

    # --- dimensions ------------------------------------------------------
    if len(dimensions) > MAX_DIMENSIONS:
        issues.append(
            ValidationIssue(
                "TOO_MANY_DIMENSIONS",
                IssueSeverity.ERROR.value,
                f"At most {MAX_DIMENSIONS} analysis dimensions are supported.",
            )
        )

    for dimension in dimensions:
        column = columns.get(dimension)
        if column is None:
            issues.append(
                ValidationIssue(
                    "DIMENSION_MISSING",
                    IssueSeverity.ERROR.value,
                    f"Dimension '{dimension}' does not exist in this dataset.",
                    column=dimension,
                )
            )
            continue
        if dimension == measure_name:
            issues.append(
                ValidationIssue(
                    "DIMENSION_IS_MEASURE",
                    IssueSeverity.ERROR.value,
                    f"'{dimension}' is already the KPI column and cannot also be a dimension.",
                    column=dimension,
                )
            )
        if dimension == time_column:
            issues.append(
                ValidationIssue(
                    "DIMENSION_IS_TIME",
                    IssueSeverity.ERROR.value,
                    f"'{dimension}' is already the time column and cannot also be a dimension.",
                    column=dimension,
                )
            )
        if column.unique_count == 1:
            issues.append(
                ValidationIssue(
                    "DIMENSION_CONSTANT",
                    IssueSeverity.WARNING.value,
                    f"'{dimension}' has a single value, so it cannot explain any change.",
                    column=dimension,
                )
            )
        elif column.unique_count and profile.row_count:
            ratio = column.unique_count / profile.row_count
            if ratio > 0.95:
                issues.append(
                    ValidationIssue(
                        "DIMENSION_UNIQUE_PER_ROW",
                        IssueSeverity.WARNING.value,
                        f"'{dimension}' is nearly unique per row and behaves like an identifier.",
                        column=dimension,
                        details={"unique_count": column.unique_count},
                    )
                )

    # --- aggregation sanity ---------------------------------------------
    if measure is not None and aggregation == Aggregation.SUM.value:
        low = measure.percentiles.get("p1")
        high = measure.percentiles.get("p99")
        if low is not None and high is not None and low >= 0 and high <= 1:
            issues.append(
                ValidationIssue(
                    "AGGREGATION_MISMATCH",
                    IssueSeverity.WARNING.value,
                    f"'{measure_name}' looks like a ratio. SUM is rarely meaningful; consider AVG.",
                    column=measure_name,
                    suggested_fix={"action": "change_aggregation", "target": Aggregation.AVG.value},
                )
            )

    return ValidationReport(_state_from(issues), issues)
