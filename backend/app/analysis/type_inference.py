"""Safe type inference with conversion confidence (PRD section 10).

The PRD requires the validator to *attempt* conversion rather than reject every
mismatch. DuckDB's ``TRY_CAST`` makes that a plain aggregate: count how many
non-null values survive the cast, and the ratio is the confidence.

Everything here is pure and computed from counts, so it is directly testable
without a database.
"""

from dataclasses import dataclass, field

from app.db.models.enums import InferredType

# Values that look present but mean "missing".
NULL_SENTINELS = ("", "na", "n/a", "null", "none", "-", "#n/a", "nan", "nil", "unknown")

# Confidence at or above which a type is accepted outright.
STRONG_CONFIDENCE = 0.98
# Lowest confidence still accepted, with the invalid values reported.
MIN_ACCEPTABLE_CONFIDENCE = 0.80
# Cleaning (stripping currency symbols, separators) costs a little confidence
# so a column that parses natively is always preferred.
CLEANING_PENALTY = 0.95

# Most specific first: a column of 0/1 is boolean before it is an integer.
TYPE_PRECEDENCE = (
    InferredType.BOOLEAN,
    InferredType.INTEGER,
    InferredType.NUMERIC,
    InferredType.DATE,
    InferredType.DATETIME,
)

DUCKDB_CAST_TARGET = {
    InferredType.BOOLEAN: "BOOLEAN",
    InferredType.INTEGER: "BIGINT",
    InferredType.NUMERIC: "DOUBLE",
    InferredType.DATE: "DATE",
    InferredType.DATETIME: "TIMESTAMP",
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
)


def confidence_label(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= 0.99:
        return "high"
    if confidence >= 0.90:
        return "medium"
    if confidence >= MIN_ACCEPTABLE_CONFIDENCE:
        return "low"
    return "none"


@dataclass
class TypeCandidate:
    inferred_type: InferredType
    confidence: float
    castable_count: int
    used_cleaning: bool = False


@dataclass
class ColumnInference:
    """The resolved type for one column plus the evidence behind it."""

    column: str
    raw_type: str
    inferred_type: InferredType
    confidence: float
    non_null_count: int
    null_count: int
    invalid_value_count: int
    requires_conversion: bool
    used_cleaning: bool = False
    candidates: dict[str, float] = field(default_factory=dict)
    sample_invalid_values: list[str] = field(default_factory=list)

    @property
    def confidence_label(self) -> str:
        return confidence_label(self.confidence)

    @property
    def is_numeric(self) -> bool:
        return self.inferred_type in {InferredType.INTEGER, InferredType.NUMERIC}

    @property
    def is_temporal(self) -> bool:
        return self.inferred_type in {InferredType.DATE, InferredType.DATETIME}


def resolve_type(
    *,
    column: str,
    raw_type: str,
    non_null_count: int,
    null_count: int,
    raw_castable: dict[InferredType, int],
    cleaned_castable: dict[InferredType, int] | None = None,
    distinct_count: int | None = None,
    has_leading_zeros: bool = False,
) -> ColumnInference:
    """Pick the best type for a column from TRY_CAST success counts."""
    cleaned_castable = cleaned_castable or {}

    if non_null_count == 0:
        # Nothing to infer from - an all-null column stays a string and is
        # flagged by the validation rules instead.
        return ColumnInference(
            column=column,
            raw_type=raw_type,
            inferred_type=InferredType.STRING,
            confidence=0.0,
            non_null_count=0,
            null_count=null_count,
            invalid_value_count=0,
            requires_conversion=False,
        )

    candidates: dict[InferredType, TypeCandidate] = {}
    for candidate_type in TYPE_PRECEDENCE:
        raw_ratio = raw_castable.get(candidate_type, 0) / non_null_count
        cleaned_ratio = cleaned_castable.get(candidate_type, 0) / non_null_count
        penalised_cleaned = cleaned_ratio * CLEANING_PENALTY
        if penalised_cleaned > raw_ratio:
            candidates[candidate_type] = TypeCandidate(
                candidate_type, penalised_cleaned, cleaned_castable.get(candidate_type, 0), True
            )
        else:
            candidates[candidate_type] = TypeCandidate(
                candidate_type, raw_ratio, raw_castable.get(candidate_type, 0), False
            )

    # Codes with leading zeros (postcodes, account numbers) are text even
    # though they parse as integers - converting them destroys data.
    if has_leading_zeros:
        candidates.pop(InferredType.INTEGER, None)
        candidates.pop(InferredType.NUMERIC, None)

    # Only treat 0/1 as boolean when there really are just two distinct values.
    if distinct_count is not None and distinct_count > 2:
        candidates.pop(InferredType.BOOLEAN, None)

    chosen: TypeCandidate | None = None
    for candidate_type in TYPE_PRECEDENCE:
        candidate = candidates.get(candidate_type)
        if candidate and candidate.confidence >= STRONG_CONFIDENCE:
            chosen = candidate
            break

    if chosen is None:
        best = max(candidates.values(), key=lambda c: c.confidence, default=None)
        if best is not None and best.confidence >= MIN_ACCEPTABLE_CONFIDENCE:
            chosen = best

    if chosen is None:
        return ColumnInference(
            column=column,
            raw_type=raw_type,
            inferred_type=InferredType.STRING,
            confidence=1.0,
            non_null_count=non_null_count,
            null_count=null_count,
            invalid_value_count=0,
            requires_conversion=False,
            candidates={t.value: round(c.confidence, 4) for t, c in candidates.items()},
        )

    declared_is_text = raw_type.upper() in {"VARCHAR", "STRING", "TEXT", "BLOB"}
    return ColumnInference(
        column=column,
        raw_type=raw_type,
        inferred_type=chosen.inferred_type,
        confidence=round(chosen.confidence, 4),
        non_null_count=non_null_count,
        null_count=null_count,
        invalid_value_count=max(0, non_null_count - chosen.castable_count),
        requires_conversion=declared_is_text or chosen.used_cleaning,
        used_cleaning=chosen.used_cleaning,
        candidates={t.value: round(c.confidence, 4) for t, c in candidates.items()},
    )


def cast_success_predicate(expression: str, target: InferredType) -> str:
    """SQL that is true when ``expression`` converts to ``target`` losslessly.

    Integers need an extra integrality check: DuckDB happily casts ``'950.50'``
    to BIGINT by truncating, so a plain TRY_CAST would classify a decimal
    column as integer and silently drop the fractional part.
    """
    cast_type = DUCKDB_CAST_TARGET[target]
    if target is InferredType.INTEGER:
        return (
            f"(TRY_CAST({expression} AS BIGINT) IS NOT NULL "
            f"AND TRY_CAST({expression} AS DOUBLE) IS NOT NULL "
            f"AND TRY_CAST({expression} AS DOUBLE) = floor(TRY_CAST({expression} AS DOUBLE)))"
        )
    return f"TRY_CAST({expression} AS {cast_type}) IS NOT NULL"


def cleaned_numeric_expression(quoted_column: str) -> str:
    """SQL that strips formatting before a numeric cast.

    Handles ``$1,200``, ``1 234.50``, ``12%`` and accounting negatives ``(500)``.
    """
    trimmed = f"trim({quoted_column})"
    accounting = (
        f"CASE WHEN {trimmed} LIKE '(%)' "
        f"THEN '-' || substr({trimmed}, 2, length({trimmed}) - 2) ELSE {trimmed} END"
    )
    return f"regexp_replace({accounting}, '[^0-9eE+\\-\\.]', '', 'g')"


def null_sentinel_predicate(quoted_column: str) -> str:
    """SQL predicate matching values that should count as missing."""
    values = ", ".join("'" + s + "'" for s in NULL_SENTINELS)
    return f"({quoted_column} IS NULL OR lower(trim({quoted_column})) IN ({values}))"
