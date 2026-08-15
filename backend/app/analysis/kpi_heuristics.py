"""KPI candidate detection (PRD section 11).

Pure scoring functions over profile data. Every candidate carries the reasons it
was chosen: PRD principle 6 requires analytical claims to be traceable, and the
recommendation is the first link in that chain.
"""

import re
from dataclasses import dataclass, field

from app.db.models.enums import Aggregation, InferredType, SemanticType

# --- name patterns -------------------------------------------------------
IDENTIFIER_NAME = re.compile(
    r"(^|_)(id|ids|key|keys|code|codes|no|num|number|guid|uuid|sku|ref|reference|barcode)$", re.I
)
MEASURE_NAME = re.compile(
    r"(revenue|sales|amount|amt|price|cost|profit|margin|qty|quantity|units|value|total|gmv|"
    r"spend|discount|volume|orders|sessions|clicks|impressions|income|expense|balance)",
    re.I,
)
RATIO_NAME = re.compile(r"(rate|pct|percent|percentage|ratio|avg|average|mean|index|score|share)", re.I)
TIME_NAME = re.compile(
    r"(date|dt|day|month|year|period|week|quarter|timestamp|time|_at$|^created|^updated|^posted)", re.I
)
DIMENSION_NAME = re.compile(
    r"(region|country|city|state|province|segment|channel|product|category|brand|store|branch|"
    r"customer|client|type|status|group|department|team|source|campaign|currency|gender|tier|"
    r"class|division|market|vendor|supplier)",
    re.I,
)

MEASURE_THRESHOLD = 0.45
DIMENSION_THRESHOLD = 0.40
TIME_THRESHOLD = 0.50


@dataclass
class ColumnFacts:
    """Everything the heuristics need, decoupled from the ORM."""

    name: str
    inferred_type: InferredType
    raw_type: str = "VARCHAR"
    conversion_confidence: float = 1.0
    null_pct: float = 0.0
    unique_count: int | None = None
    row_count: int = 0
    min_value: float | None = None
    max_value: float | None = None
    distinct_periods: int | None = None
    avg_text_length: float | None = None

    @property
    def non_null_count(self) -> int:
        return max(0, int(round(self.row_count * (1 - self.null_pct / 100))))

    @property
    def distinct_ratio(self) -> float:
        if not self.unique_count or self.non_null_count == 0:
            return 0.0
        return self.unique_count / self.non_null_count

    @property
    def is_numeric(self) -> bool:
        return self.inferred_type in {InferredType.INTEGER, InferredType.NUMERIC}

    @property
    def is_temporal(self) -> bool:
        return self.inferred_type in {InferredType.DATE, InferredType.DATETIME}


@dataclass
class Candidate:
    column: str
    score: float
    reasons: list[str] = field(default_factory=list)
    suggested_aggregation: str | None = None


@dataclass
class KpiCandidates:
    measures: list[Candidate] = field(default_factory=list)
    time_columns: list[Candidate] = field(default_factory=list)
    dimensions: list[Candidate] = field(default_factory=list)
    semantic_types: dict[str, SemanticType] = field(default_factory=dict)


def is_identifier(facts: ColumnFacts) -> bool:
    """Detect key-like columns.

    This runs first and excludes the column from measures - it is what stops
    ``order_id`` being proposed as a KPI, which is the most damaging thing a
    naive numeric-column heuristic does.
    """
    if IDENTIFIER_NAME.search(facts.name):
        return True
    # Near-unique values with no repetition cannot aggregate meaningfully.
    if facts.unique_count and facts.distinct_ratio > 0.95 and facts.non_null_count > 20:
        return facts.inferred_type in {InferredType.INTEGER, InferredType.STRING}
    return False


def score_time(facts: ColumnFacts) -> Candidate | None:
    reasons: list[str] = []

    if facts.is_temporal:
        temporal_confidence = facts.conversion_confidence
        reasons.append(f"parsed as {facts.inferred_type.value}")
    elif facts.inferred_type == InferredType.INTEGER and TIME_NAME.search(facts.name):
        # Year / yyyymm columns stored as integers.
        in_year_range = facts.min_value is not None and 1900 <= facts.min_value <= 2200
        in_yyyymm_range = facts.min_value is not None and 190001 <= facts.min_value <= 220012
        if not (in_year_range or in_yyyymm_range):
            return None
        temporal_confidence = 0.85
        reasons.append("integer period column (needs conversion)")
    else:
        return None

    name_hint = 1.0 if TIME_NAME.search(facts.name) else 0.0
    if name_hint:
        reasons.append("name looks like a time field")

    completeness = 1 - facts.null_pct / 100
    periods = min(facts.distinct_periods or 0, 24) / 24
    if facts.distinct_periods:
        reasons.append(f"{facts.distinct_periods} distinct periods")

    score = 0.60 * temporal_confidence + 0.20 * name_hint + 0.10 * completeness + 0.10 * periods
    if score < TIME_THRESHOLD:
        return None
    return Candidate(column=facts.name, score=round(score, 4), reasons=reasons)


def _additivity(facts: ColumnFacts) -> tuple[float, str | None]:
    """How safely the column can be summed."""
    if RATIO_NAME.search(facts.name):
        return 0.3, "looks like a rate or ratio - AVG is usually correct"
    if (
        facts.min_value is not None
        and facts.max_value is not None
        and facts.min_value >= 0
        and facts.max_value <= 1
    ):
        return 0.3, "values bounded in [0, 1] - summing is rarely meaningful"
    return 1.0, None


def score_measure(facts: ColumnFacts) -> Candidate | None:
    if is_identifier(facts):
        return None
    if not facts.is_numeric or facts.conversion_confidence < 0.80:
        return None
    if facts.null_pct >= 50 or (facts.unique_count is not None and facts.unique_count <= 2):
        return None

    reasons: list[str] = []
    if facts.conversion_confidence >= 0.99:
        reasons.append("numeric with high conversion confidence")
    else:
        reasons.append(f"numeric with {facts.conversion_confidence:.0%} conversion confidence")

    name_hint = 1.0 if MEASURE_NAME.search(facts.name) else 0.0
    if name_hint:
        reasons.append("name matches a business-metric pattern")

    completeness = 1 - facts.null_pct / 100
    if facts.null_pct == 0:
        reasons.append("no missing values")

    additivity, additivity_reason = _additivity(facts)
    if additivity_reason:
        reasons.append(additivity_reason)

    score = 0.35 * facts.conversion_confidence + 0.30 * name_hint + 0.15 * completeness + 0.20 * additivity
    if score < MEASURE_THRESHOLD:
        return None

    aggregation = Aggregation.AVG.value if additivity < 0.8 else Aggregation.SUM.value
    return Candidate(
        column=facts.name, score=round(score, 4), reasons=reasons, suggested_aggregation=aggregation
    )


def _cardinality_fit(unique_count: int) -> float:
    """Reward the cardinality range that makes a useful drill-down."""
    if unique_count < 2:
        return 0.0
    if unique_count <= 2:
        return 0.4
    if unique_count <= 50:
        return 1.0
    if unique_count <= 200:
        # Linear decay 1.0 -> 0.4 across 50..200
        return 1.0 - 0.6 * (unique_count - 50) / 150
    if unique_count <= 1000:
        return 0.4 - 0.2 * (unique_count - 200) / 800
    return 0.0


def score_dimension(facts: ColumnFacts) -> Candidate | None:
    if facts.is_temporal or facts.unique_count is None:
        return None
    if facts.null_pct >= 50:
        return None
    if facts.unique_count < 2:
        return None
    # Free text is not a dimension.
    if facts.avg_text_length is not None and facts.avg_text_length > 100:
        return None

    ceiling = min(1000, max(2, int(0.5 * facts.row_count))) if facts.row_count else 1000
    if facts.unique_count > ceiling or facts.distinct_ratio > 0.5:
        return None
    # A pure measure should not also be offered as a dimension.
    if facts.is_numeric and facts.unique_count > 50:
        return None

    reasons = [f"{facts.unique_count} distinct values"]
    name_hint = 1.0 if DIMENSION_NAME.search(facts.name) else 0.0
    if name_hint:
        reasons.append("name matches a business-dimension pattern")

    fit = _cardinality_fit(facts.unique_count)
    if fit >= 1.0:
        reasons.append("cardinality is well suited to drill-down")
    completeness = 1 - facts.null_pct / 100

    score = 0.50 * fit + 0.30 * name_hint + 0.20 * completeness

    # A low-cardinality numeric column (a rating, a band) can be a valid
    # dimension, but it is usually a measure. Rank it below genuine
    # categoricals rather than excluding it outright.
    if facts.is_numeric:
        score *= 0.6
        reasons.append("numeric column - usable as a dimension but more likely a measure")

    if score < DIMENSION_THRESHOLD:
        return None
    return Candidate(column=facts.name, score=round(score, 4), reasons=reasons)


def detect(columns: list[ColumnFacts]) -> KpiCandidates:
    """Classify every column and rank the candidates."""
    result = KpiCandidates()

    for facts in columns:
        time_candidate = score_time(facts)
        measure_candidate = score_measure(facts)
        dimension_candidate = score_dimension(facts)

        if time_candidate:
            result.time_columns.append(time_candidate)
        if measure_candidate:
            result.measures.append(measure_candidate)
        if dimension_candidate:
            result.dimensions.append(dimension_candidate)

        # A column's primary role, for the profile's semantic_type column.
        if time_candidate:
            result.semantic_types[facts.name] = SemanticType.TIME
        elif is_identifier(facts):
            result.semantic_types[facts.name] = SemanticType.IDENTIFIER
        elif measure_candidate and (
            not dimension_candidate or measure_candidate.score >= dimension_candidate.score
        ):
            result.semantic_types[facts.name] = SemanticType.MEASURE
        elif dimension_candidate:
            result.semantic_types[facts.name] = SemanticType.DIMENSION
        else:
            result.semantic_types[facts.name] = SemanticType.UNKNOWN

    result.measures.sort(key=lambda c: c.score, reverse=True)
    result.time_columns.sort(key=lambda c: c.score, reverse=True)
    result.dimensions.sort(key=lambda c: c.score, reverse=True)
    return result


def recommended_default(candidates: KpiCandidates, detected_frequency: str | None = None) -> dict:
    """A ready-to-submit KPI definition the user can accept or override."""
    if not candidates.measures:
        return {}

    measure = candidates.measures[0]
    time_column = candidates.time_columns[0].column if candidates.time_columns else None
    dimensions = [c.column for c in candidates.dimensions[:3]]

    comparison = "previous_period"
    if detected_frequency == "yearly":
        comparison = "previous_year"

    return {
        "name": measure.column.replace("_", " ").title(),
        "column": measure.column,
        "aggregation": measure.suggested_aggregation or Aggregation.SUM.value,
        "time_column": time_column,
        "dimensions": dimensions,
        "comparison": comparison,
    }
