"""SQL expression builders that branch on a column's PHYSICAL type.

The normalized Parquet does not have uniform types. CSV keeps DuckDB's sniffed
types, but ``materialize.excel_to_parquet`` and ``materialize.rows_to_parquet``
write every column as a string - so an Excel or SQL Server dataset arrives with
a VARCHAR revenue column even though the profile says it is numeric.

``ColumnProfile.inferred_type`` cannot answer this: it describes the original
stage-A VARCHAR scan, and it does not persist ``used_cleaning``, which
``profiler._typed_expression`` needs. So the source of truth here is a single
``DESCRIBE`` against the relation actually being read.

Every function in this module returns a SQL string. Nothing here executes.
"""

from app.analysis.duckdb_session import quote_identifier
from app.analysis.type_inference import (
    DATE_FORMATS,
    cleaned_numeric_expression,
    null_sentinel_predicate,
)
from app.db.models.enums import Aggregation

NUMERIC_PHYSICAL = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "REAL",
    "DOUBLE",
    "DECIMAL",
    "NUMERIC",
)
TEMPORAL_PHYSICAL = ("DATE", "TIMESTAMP", "DATETIME")
TEXT_PHYSICAL = ("VARCHAR", "STRING", "TEXT", "CHAR", "BLOB")

# Aggregations that do not require the measure to be a number.
COUNTING = frozenset({Aggregation.COUNT, Aggregation.COUNT_DISTINCT})

# DuckDB aggregate function per KPI aggregation. A literal map, never string
# formatting from the stored value - an unknown member must fail before it can
# reach generated SQL.
AGGREGATE_FUNCTION: dict[Aggregation, str] = {
    Aggregation.SUM: "sum",
    Aggregation.AVG: "avg",
    Aggregation.COUNT: "count",
    Aggregation.COUNT_DISTINCT: "count",  # DISTINCT is added by the builder
    Aggregation.MIN: "min",
    Aggregation.MAX: "max",
    Aggregation.MEDIAN: "median",
}


def _base_type(physical_type: str) -> str:
    """Strip parameters: ``DECIMAL(18,2)`` -> ``DECIMAL``."""
    return physical_type.upper().split("(")[0].strip()


def is_numeric_physical(physical_type: str) -> bool:
    return _base_type(physical_type) in NUMERIC_PHYSICAL


def is_temporal_physical(physical_type: str) -> bool:
    return _base_type(physical_type).startswith(TEMPORAL_PHYSICAL)


def is_text_physical(physical_type: str) -> bool:
    return _base_type(physical_type) in TEXT_PHYSICAL


def is_boolean_physical(physical_type: str) -> bool:
    return _base_type(physical_type) == "BOOLEAN"


def aggregate_expression(aggregation: Aggregation, inner: str) -> str:
    """Wrap ``inner`` in the aggregate for this KPI."""
    function = AGGREGATE_FUNCTION[aggregation]
    if aggregation is Aggregation.COUNT_DISTINCT:
        return f"count(DISTINCT {inner})"
    return f"{function}({inner})"


def measure_expression(column: str, physical_type: str, aggregation: Aggregation) -> str:
    """A value expression for the KPI measure.

    Counting aggregations do not need a number, so they keep the text form with
    null sentinels folded out - otherwise ``'n/a'`` would inflate a distinct
    count. Everything else yields a DOUBLE.
    """
    quoted = quote_identifier(column)

    if aggregation in COUNTING:
        text = f"CAST({quoted} AS VARCHAR)"
        return f"CASE WHEN {null_sentinel_predicate(text)} THEN NULL ELSE trim({text}) END"

    if is_numeric_physical(physical_type):
        # Widen explicitly: a DECIMAL sum can overflow its own precision, and
        # an integer average truncates in some engines.
        return f"CAST({quoted} AS DOUBLE)"

    if is_boolean_physical(physical_type):
        return f"CAST(CAST({quoted} AS TINYINT) AS DOUBLE)"

    if is_temporal_physical(physical_type):
        # No sensible numeric reading of a date; the engine rejects this.
        return ""

    # Text. Native cast first so a clean column never pays for regexp_replace,
    # with the cleaning expression as a rescue for '$1,200' and '(500)'.
    # NOTE: cleaned_numeric_expression turns '12%' into 12, not 0.12.
    return (
        f"CASE WHEN {null_sentinel_predicate(quoted)} THEN NULL "
        f"ELSE COALESCE(TRY_CAST(trim({quoted}) AS DOUBLE), "
        f"TRY_CAST({cleaned_numeric_expression(quoted)} AS DOUBLE)) END"
    )


def time_expression(column: str, physical_type: str) -> str:
    """A TIMESTAMP expression for the KPI time column.

    DATE and every TIMESTAMP width normalise to TIMESTAMP so the period
    predicates are uniform. Returns "" when no valid reading exists.
    """
    quoted = quote_identifier(column)

    if is_temporal_physical(physical_type):
        return f"CAST({quoted} AS TIMESTAMP)"

    if is_numeric_physical(physical_type) or is_boolean_physical(physical_type):
        # Deliberately no epoch heuristics: guessing seconds vs milliseconds
        # would silently place the data in the wrong century.
        return ""

    attempts = [f"TRY_CAST(trim({quoted}) AS TIMESTAMP)"]
    attempts += [f"try_strptime(trim({quoted}), '{fmt}')" for fmt in DATE_FORMATS]
    return (
        f"CASE WHEN {null_sentinel_predicate(quoted)} THEN NULL "
        f"ELSE COALESCE({', '.join(attempts)}) END"
    )


def dimension_expression(column: str, physical_type: str) -> str:
    """A VARCHAR expression for a dimension.

    SQL NULL is deliberately preserved rather than folded into a sentinel
    string: a literal '(unknown)' in the data would silently merge with genuine
    nulls, and every row must land in exactly one cell for the decomposition to
    hold. The null group is labelled at the presentation layer instead.
    """
    quoted = quote_identifier(column)
    if is_text_physical(physical_type):
        return f"trim({quoted})"
    return f"CAST({quoted} AS VARCHAR)"


def day_first_ambiguity_expressions(column: str) -> tuple[str, str]:
    """A pair of counts that reveal an ambiguous day/month date column.

    ``DATE_FORMATS`` puts %d/%m/%Y ahead of %m/%d/%Y, which silently decides
    03/04/2026. When both orderings parse the whole column the engine says so
    rather than letting the ordering make the call invisibly.
    """
    quoted = quote_identifier(column)
    day_first = f"count(try_strptime(trim({quoted}), '%d/%m/%Y'))"
    month_first = f"count(try_strptime(trim({quoted}), '%m/%d/%Y'))"
    return day_first, month_first
